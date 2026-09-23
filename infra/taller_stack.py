from pathlib import Path

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integraciones,
    aws_bedrock as bedrock,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_scheduler as scheduler,
    custom_resources as cr,
)
from constructs import Construct

RAIZ = Path(__file__).resolve().parents[1]
GRUPO_RECORDATORIOS = "taller-recordatorios"
PREFIJOS_PERFIL = {"us", "eu", "apac", "jp", "au", "ca", "global"}  # perfiles de inferencia entre regiones

# CloudFormation no crea parámetros SecureString, así que un recurso personalizado genera las claves (panel y chat)
# al desplegar (nunca aparecen en la plantilla) y las borra al destruir el stack. Parameter Store estándar no cuesta.
# Si el parámetro ya existe no lo toca: un redespliegue no cambia las claves.
CODIGO_CLAVES = """
import secrets
import boto3

ssm = boto3.client("ssm")
# Sin 0/O ni 1/I/L: el código del chat se dicta o se escribe en el celular.
ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def nueva_clave(formato):
    if formato == "codigo":
        return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(10))
    return secrets.token_urlsafe(18)


def handler(evento, _contexto):
    props = evento["ResourceProperties"]
    nombre = props["Nombre"]
    if evento["RequestType"] == "Delete":
        try:
            ssm.delete_parameter(Name=nombre)
        except ssm.exceptions.ParameterNotFound:
            pass
    else:
        try:
            ssm.put_parameter(Name=nombre, Type="SecureString", Value=nueva_clave(props.get("Formato")),
                              Overwrite=False, Description=props.get("Descripcion", "Clave del taller (demo)"))
        except ssm.exceptions.ParameterAlreadyExists:
            pass
    return {"PhysicalResourceId": nombre}
"""


class TallerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ctx = self.node.try_get_context
        model_id = ctx("modelId") or "us.amazon.nova-2-lite-v1:0"
        vision_model_id = ctx("visionModelId") or model_id
        razonamiento = ctx("razonamiento") if ctx("razonamiento") is not None else "low"
        sender_email = ctx("senderEmail") or ""
        modo_recordatorio = ctx("modoRecordatorio") or "demo"

        # ---------- datos ----------
        def tabla(nombre: str, pk: str, sk: str | None = None) -> ddb.Table:
            return ddb.Table(
                self, nombre,
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name=sk, type=ddb.AttributeType.STRING) if sk else None,
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                time_to_live_attribute="ttl",
                removal_policy=RemovalPolicy.DESTROY,
            )

        conversaciones = tabla("Conversaciones", "session_id")
        mensajes = tabla("Mensajes", "session_id", "sk")
        citas = tabla("Citas", "cita_id")
        citas.add_global_secondary_index(
            index_name="por_fecha",
            partition_key=ddb.Attribute(name="fecha", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="hora", type=ddb.AttributeType.STRING),
        )
        citas.add_global_secondary_index(
            index_name="por_telefono",
            partition_key=ddb.Attribute(name="telefono", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="fecha", type=ddb.AttributeType.STRING),
        )

        # ---------- sitio estático (chat + panel) ----------
        sitio = s3.Bucket(
            self, "Sitio",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        distribucion = cloudfront.Distribution(
            self, "Distribucion",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(sitio),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
        )
        origen_sitio = f"https://{distribucion.distribution_domain_name}"
        origenes = [origen_sitio, "http://localhost:8000"]

        # ---------- fotos de los vehículos ----------
        fotos = s3.Bucket(
            self, "Fotos",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(7))],
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.POST], allowed_origins=origenes,
                              allowed_headers=["*"])],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ---------- guardrail ----------
        filtro = bedrock.CfnGuardrail.ContentFilterConfigProperty
        guardrail = bedrock.CfnGuardrail(
            self, "Guardrail",
            name=f"{construct_id}-guardrail",
            description="Guardrail del agente de citas del taller",
            blocked_input_messaging="No puedo ayudar con eso. ¿Te ayudo a agendar una cita para tu vehículo?",
            blocked_outputs_messaging="Perdón, no puedo responder eso. ¿Te ayudo con tu cita?",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(filters_config=[
                *[filtro(type=t, input_strength="HIGH", output_strength="HIGH")
                  for t in ("SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT")],
                filtro(type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"),
            ]),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(topics_config=[
                bedrock.CfnGuardrail.TopicConfigProperty(
                    name="Fraude vehicular",
                    definition="Solicitudes para alterar odómetros, placas, números de serie o documentos del "
                               "vehículo, o para evadir verificaciones o inspecciones.",
                    examples=["¿Pueden bajarle el kilometraje a mi auto?",
                              "Necesito cambiar el número de serie del motor"],
                    type="DENY",
                ),
            ]),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="BLOCK")
                    for t in ("CREDIT_DEBIT_CARD_NUMBER", "CREDIT_DEBIT_CARD_CVV", "PASSWORD")
                ]),
        )
        version_guardrail = bedrock.CfnGuardrailVersion(self, "GuardrailVersion",
                                                         guardrail_identifier=guardrail.attr_guardrail_id)

        # ---------- claves del panel y del chat (SSM Parameter Store, SecureString) ----------
        # Sin "/" inicial: así el comando de salida funciona igual en Git Bash (no convierte rutas).
        parametro_panel = f"{construct_id}-clave-panel"
        parametro_chat = f"{construct_id}-clave-chat"

        def arn_parametro(nombre: str) -> str:
            return f"arn:aws:ssm:{self.region}:{self.account}:parameter/{nombre}"

        # Los IDs "...ClavePanel" vienen de cuando solo existía esa clave. No los cambies: CloudFormation no permite
        # modificar el ServiceToken de un recurso personalizado ya creado y el despliegue fallaría.
        generador_claves = lambda_.Function(
            self, "GeneradorClavePanel",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(CODIGO_CLAVES),
            timeout=Duration.seconds(30),
            log_group=logs.LogGroup(self, "GeneradorClavePanelLogs", retention=logs.RetentionDays.ONE_WEEK,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        generador_claves.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:PutParameter", "ssm:DeleteParameter"],
            resources=[arn_parametro(parametro_panel), arn_parametro(parametro_chat)]))
        proveedor_claves = cr.Provider(self, "ProveedorClavePanel", on_event_handler=generador_claves).service_token
        clave_panel = CustomResource(self, "ClavePanelParametro", service_token=proveedor_claves,
                                     properties={"Nombre": parametro_panel,
                                                 "Descripcion": "Clave del panel del taller (demo)"})
        clave_chat = CustomResource(self, "ClaveChatParametro", service_token=proveedor_claves,
                                    properties={"Nombre": parametro_chat, "Formato": "codigo",
                                                "Descripcion": "Código de acceso al chat del taller (demo)"})

        # ---------- Lambdas ----------
        codigo = lambda_.Code.from_asset(
            str(RAIZ / "backend"),
            bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=["bash", "-c",
                         "pip install --no-cache-dir -r requirements.txt -t /asset-output && "
                         "cp -r taller /asset-output/"],
            ),
        )
        entorno = {
            "MODEL_ID": model_id,
            "VISION_MODEL_ID": vision_model_id,
            "RAZONAMIENTO": razonamiento,
            "TABLA_CONVERSACIONES": conversaciones.table_name,
            "TABLA_MENSAJES": mensajes.table_name,
            "TABLA_CITAS": citas.table_name,
            "BUCKET_FOTOS": fotos.bucket_name,
            "SCHEDULE_GROUP": GRUPO_RECORDATORIOS,
            "MODO_RECORDATORIO": modo_recordatorio,
            "SENDER_EMAIL": sender_email,
            "GUARDRAIL_ID": guardrail.attr_guardrail_id,
            "GUARDRAIL_VERSION": version_guardrail.attr_version,
        }

        def funcion(nombre: str, handler: str, timeout: int, memoria: int = 512) -> lambda_.Function:
            return lambda_.Function(
                self, nombre,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.X86_64,
                code=codigo,
                handler=handler,
                timeout=Duration.seconds(timeout),
                memory_size=memoria,
                environment=entorno,
                log_group=logs.LogGroup(self, f"{nombre}Logs", retention=logs.RetentionDays.ONE_WEEK,
                                        removal_policy=RemovalPolicy.DESTROY),
            )

        recordatorio_fn = funcion("Recordatorio", "taller.handler.recordatorio", 30, 256)
        worker_fn = funcion("Agente", "taller.handler.procesar", 300, 1024)
        api_fn = funcion("Api", "taller.handler.api", 15, 512)

        rol_scheduler = iam.Role(self, "RolScheduler", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        recordatorio_fn.grant_invoke(rol_scheduler)
        grupo = scheduler.CfnScheduleGroup(self, "GrupoRecordatorios", name=GRUPO_RECORDATORIOS)

        worker_fn.add_environment("RECORDATORIO_FUNCTION_ARN", recordatorio_fn.function_arn)
        worker_fn.add_environment("SCHEDULER_ROLE_ARN", rol_scheduler.role_arn)
        api_fn.add_environment("WORKER_FUNCTION", worker_fn.function_name)
        api_fn.add_environment("PANEL_PARAMETRO", parametro_panel)
        api_fn.add_environment("CHAT_PARAMETRO", parametro_chat)
        api_fn.node.add_dependency(clave_panel, clave_chat)
        api_fn.add_environment("RECORDATORIO_FUNCTION_ARN", recordatorio_fn.function_arn)

        # permisos mínimos por función
        for t in (conversaciones, mensajes, citas):
            t.grant_read_write_data(worker_fn)
            t.grant_read_write_data(api_fn)
        mensajes.grant_read_write_data(recordatorio_fn)
        citas.grant_read_write_data(recordatorio_fn)
        fotos.grant_read(worker_fn)
        fotos.grant_put(api_fn)
        fotos.grant_read(api_fn)
        worker_fn.grant_invoke(api_fn)
        # La llave administrada aws/ssm ya permite descifrar vía SSM a quien tenga ssm:GetParameter en la cuenta.
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"], resources=[arn_parametro(parametro_panel), arn_parametro(parametro_chat)]))

        # Converse usa el permiso bedrock:InvokeModel. Un perfil de inferencia ("us.", "global.") enruta a varias
        # regiones, así que se permite el perfil y el modelo base en cualquier región, solo para los modelos configurados.
        recursos_modelo = set()
        for modelo in {model_id, vision_model_id}:
            base = modelo.split(".", 1)[1] if modelo.split(".", 1)[0] in PREFIJOS_PERFIL else modelo
            recursos_modelo.add(f"arn:aws:bedrock:*::foundation-model/{base}")
            if base != modelo:
                recursos_modelo.add(f"arn:aws:bedrock:*:{self.account}:inference-profile/{modelo}")
        worker_fn.add_to_role_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel"],
                                                         resources=sorted(recursos_modelo)))
        worker_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:ApplyGuardrail"], resources=[guardrail.attr_guardrail_arn],
        ))
        horarios_arn = f"arn:aws:scheduler:{self.region}:{self.account}:schedule/{GRUPO_RECORDATORIOS}/*"
        worker_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["scheduler:CreateSchedule", "scheduler:DeleteSchedule"], resources=[horarios_arn]))
        api_fn.add_to_role_policy(iam.PolicyStatement(actions=["scheduler:DeleteSchedule"], resources=[horarios_arn]))
        for fn in (worker_fn, api_fn):
            fn.add_to_role_policy(iam.PolicyStatement(actions=["iam:PassRole"], resources=[rol_scheduler.role_arn]))
        if sender_email:
            for fn in (worker_fn, recordatorio_fn):
                fn.add_to_role_policy(iam.PolicyStatement(
                    actions=["ses:SendEmail"],
                    resources=[f"arn:aws:ses:{self.region}:{self.account}:identity/*"]))
        worker_fn.node.add_dependency(grupo)

        # ---------- HTTP API ----------
        api = apigw.HttpApi(
            self, "HttpApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=origenes,
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST],
                allow_headers=["content-type", "x-panel-key", "x-chat-key"],
                max_age=Duration.hours(1),
            ),
        )
        integracion = integraciones.HttpLambdaIntegration("ApiIntegracion", api_fn)
        for metodo, ruta in [("POST", "/chat"), ("GET", "/mensajes"), ("POST", "/fotos"),
                             ("GET", "/panel/citas"), ("POST", "/panel/citas/{cita_id}/estado"),
                             ("GET", "/panel/foto")]:
            api.add_routes(path=ruta, methods=[apigw.HttpMethod(metodo)], integration=integracion)
        etapa = api.default_stage.node.default_child
        etapa.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=20, throttling_rate_limit=10)

        # ---------- despliegue del frontend ----------
        s3deploy.BucketDeployment(
            self, "DesplegarSitio",
            destination_bucket=sitio,
            sources=[s3deploy.Source.asset(str(RAIZ / "frontend"), exclude=["config.json"]),
                     s3deploy.Source.json_data("config.json", {"apiUrl": api.api_endpoint})],
            distribution=distribucion,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "ChatUrl", value=origen_sitio)
        CfnOutput(self, "PanelUrl", value=f"{origen_sitio}/panel.html")
        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        for salida, parametro in (("PanelKeyCommand", parametro_panel), ("ChatKeyCommand", parametro_chat)):
            CfnOutput(self, salida, value=f"aws ssm get-parameter --name {parametro} --with-decryption "
                                          f"--region {self.region} --query Parameter.Value --output text")
