variable "prefix" {
  description = "Prefijo para todos los nombres de recursos."
  type        = string
}

variable "env" {
  description = "Nombre del ambiente (dev, staging, etc.)."
  type        = string
}

variable "coordinator_origin_domain_name" {
  description = "DNS público (de la Elastic IP) del coordinador, usado como custom origin de CloudFront para /api/*."
  type        = string
}

variable "api_port" {
  description = "Puerto donde escucha la API del coordinador."
  type        = number
  default     = 8000
}

variable "origin_verify_secret" {
  description = "Secreto para el custom header X-Origin-Verify, generado en infra/main.tf (random_password) para evitar un ciclo de dependencia con modules/compute."
  type        = string
  sensitive   = true
}
