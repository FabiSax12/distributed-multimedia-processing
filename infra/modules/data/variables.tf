variable "prefix" {
  description = "Prefijo para todos los nombres de recursos."
  type        = string
}

variable "env" {
  description = "Nombre del ambiente (dev, staging, etc.)."
  type        = string
}

variable "uploads_expiration_days" {
  description = "Días hasta que expira (se borra) lo que haya bajo uploads/ en el bucket de dataset."
  type        = number
  default     = 30
}
