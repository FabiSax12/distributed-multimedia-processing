provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "distributed-multimedia-processing"
      Env     = var.env
    }
  }
}

module "queues" {
  source = "./modules/queues"

  prefix = var.prefix
  env    = var.env

  visibility_timeout_video     = var.visibility_timeout_video
  visibility_timeout_audio     = var.visibility_timeout_audio
  visibility_timeout_metadatos = var.visibility_timeout_metadatos
}

module "data" {
  source = "./modules/data"

  prefix = var.prefix
  env    = var.env
}

# Secreto compartido para el header X-Origin-Verify. Se genera acá (no
# dentro de modules/compute ni modules/frontend) porque cada módulo necesita
# un output del otro (frontend necesita el DNS del coordinador; compute
# necesita el secreto) y eso crearía un ciclo de dependencia entre módulos.
resource "random_password" "origin_verify_secret" {
  length  = 32
  special = false
}

module "compute" {
  source = "./modules/compute"

  prefix = var.prefix
  env    = var.env

  api_port     = var.api_port
  worker_count = var.worker_count

  queue_urls = module.queues.queue_urls
  queue_arns = module.queues.queue_arns

  table_cases_name    = module.data.cases_table_name
  table_cases_arn     = module.data.cases_table_arn
  table_subtasks_name = module.data.subtasks_table_name
  table_subtasks_arn  = module.data.subtasks_table_arn
  table_workers_name  = module.data.workers_table_name
  table_workers_arn   = module.data.workers_table_arn

  dataset_bucket_name = module.data.dataset_bucket_name
  dataset_bucket_arn  = module.data.dataset_bucket_arn
  results_bucket_name = module.data.results_bucket_name
  results_bucket_arn  = module.data.results_bucket_arn

  origin_verify_secret = random_password.origin_verify_secret.result
}

module "frontend" {
  source = "./modules/frontend"

  prefix = var.prefix
  env    = var.env

  coordinator_origin_domain_name = module.compute.coordinator_public_dns
  api_port                       = var.api_port
  origin_verify_secret           = random_password.origin_verify_secret.result
}

# CORS de los buckets de datos: dependen del dominio de CloudFront, que a su
# vez depende del bucket del sitio (modules/frontend). Se definen acá,
# después de instanciar tanto modules/data como modules/frontend, para no
# introducir un ciclo entre módulos.
resource "aws_s3_bucket_cors_configuration" "dataset" {
  bucket = module.data.dataset_bucket_id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = ["https://${module.frontend.cloudfront_domain_name}"]
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_cors_configuration" "results" {
  bucket = module.data.results_bucket_id

  cors_rule {
    allowed_methods = ["GET"]
    allowed_origins = ["https://${module.frontend.cloudfront_domain_name}"]
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

# Budget mensual con alerta por correo al 80% y al 100%.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.prefix}-${var.env}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_amount)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Archivo con las variables de entorno para correr coordinador y workers
# desde la laptop contra los recursos reales (sin ROLE ni POOL, esos son
# por-instancia). Va al .gitignore de la raíz del proyecto.
resource "local_sensitive_file" "env_local" {
  filename = "${path.module}/../.env.local"

  content = <<-EOT
    AWS_REGION=${var.region}
    QUEUE_URLS=${jsonencode(module.queues.queue_urls)}
    TABLE_CASES=${module.data.cases_table_name}
    TABLE_SUBTASKS=${module.data.subtasks_table_name}
    TABLE_WORKERS=${module.data.workers_table_name}
    DATASET_BUCKET=${module.data.dataset_bucket_name}
    RESULTS_BUCKET=${module.data.results_bucket_name}
    API_PORT=${var.api_port}
    ORIGIN_VERIFY_SECRET=${random_password.origin_verify_secret.result}
  EOT
}
