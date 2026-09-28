variable "region" {
  description = "Región de AWS."
  type        = string
  default     = "us-east-1"
}

variable "prefix" {
  description = "Prefijo para todos los nombres de recursos."
  type        = string
  default     = "dmp"
}

variable "env" {
  description = "Nombre del ambiente."
  type        = string
  default     = "dev"
}

variable "api_port" {
  description = "Puerto de la API del coordinador."
  type        = number
  default     = 8000
}

variable "worker_count" {
  description = "Cantidad de instancias por pool de worker. Escalar un pool es solo cambiar su número acá y volver a aplicar."
  type = object({
    video     = number
    audio     = number
    metadatos = number
  })
  default = {
    video     = 1
    audio     = 1
    metadatos = 1
  }
}

variable "visibility_timeout_video" {
  type    = number
  default = 900
}

variable "visibility_timeout_audio" {
  type    = number
  default = 300
}

variable "visibility_timeout_metadatos" {
  type    = number
  default = 120
}

variable "budget_amount" {
  description = "Monto mensual (USD) del budget de AWS."
  type        = number
  default     = 10
}

variable "alert_email" {
  description = "Correo que recibe las alertas del budget (80% y 100%). Sin default: hay que pasarlo explícitamente."
  type        = string
}
