# Infraestructura (Terraform)

Plataforma distribuida de procesamiento multimedia por casos.

```
infra/
  modules/
    queues/          # SQS: seis colas de trabajo + resultados + DLQ
    data/            # DynamoDB (Cases/SubTasks/Workers) + buckets dataset/resultados
    compute/         # EC2 (coordinador + workers), security groups, IAM, Elastic IP
    frontend/        # bucket del sitio + CloudFront
  main.tf            # compone los módulos
  variables.tf
  outputs.tf
  versions.tf
```

## Orden de aplicación

Un solo directorio (`infra/`), sin backend remoto: el estado queda local en
`infra/terraform.tfstate` (gitignored).

```bash
cd infra
terraform init
terraform plan -var="alert_email=tu-correo@ejemplo.com"
terraform apply -var="alert_email=tu-correo@ejemplo.com"
```

`alert_email` no tiene default: es el correo que recibe las alertas del
budget mensual (80% y 100%). Pasalo con `-var` o en un `*.tfvars` local (no
versionado — ver `.gitignore`).

## Escalar un pool de workers

Editá `worker_count` (en `infra/variables.tf` o pasándolo por `-var`) y
volvé a aplicar. Por ejemplo, para subir el pool de video a 3 instancias:

```bash
terraform apply -var='worker_count={video=3,audio=1,metadatos=1}' -var="alert_email=..."
```

No hace falta tocar nada más: los `aws_instance.worker_*` usan `count =
var.worker_count.<pool>`.

## Entrar a una instancia (SSM Session Manager)

No hay key pair ni puerto 22 abierto. El acceso es por SSM (rol
`AmazonSSMManagedInstanceCore` adjunto a los dos roles de instancia):

```bash
aws ssm start-session --target <instance-id>
```

Los IDs de instancia están en el output `instance_ids` de `infra`
(`terraform output instance_ids`).

## Notas de diseño

- **CORS de los buckets de dataset/resultados**: viven en `infra/main.tf`
  (no dentro de `modules/data`) porque dependen del dominio de CloudFront, y
  el dominio de CloudFront depende del bucket del sitio (`modules/frontend`).
  Definirlos en el nivel de composición evita un ciclo de dependencia entre
  módulos.
- **Secreto `X-Origin-Verify`**: se genera con `random_password` en
  `infra/main.tf` (no dentro de `modules/compute` ni `modules/frontend`),
  porque frontend necesita el DNS del coordinador (de compute) y compute
  necesita el secreto (de frontend) — generarlo en el nivel de composición
  evita el ciclo.
- **DLQ y colas de trabajo**: la DLQ calcula los ARNs de las seis colas de
  trabajo a partir del formato determinístico de ARN de SQS (sin depender
  del recurso), para no crear un ciclo con el `redrive_policy` de las colas
  de trabajo (que sí depende del ARN real de la DLQ).
- **`.env.local`**: lo genera `local_sensitive_file` en `infra/main.tf`, con
  las mismas variables que usan las EC2 (sin `ROLE` ni `POOL`), para correr
  coordinador y workers desde la laptop contra los recursos reales. Queda en
  la raíz del proyecto (`${path.module}/../.env.local`) y está en
  `.gitignore` de la raíz del proyecto.
- **Despliegue de la aplicación**: fuera de alcance de esta infraestructura.
  Los templates `user_data` (`modules/compute/templates/*.sh.tftpl`) dejan un
  comentario `TODO` marcando dónde va: clonar el repo y crear la unidad
  systemd.
