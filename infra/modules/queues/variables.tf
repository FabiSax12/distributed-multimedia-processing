variable "prefix" {
  description = "Prefijo para todos los nombres de recursos."
  type        = string
}

variable "env" {
  description = "Nombre del ambiente (dev, staging, etc.)."
  type        = string
}

variable "visibility_timeout_video" {
  description = "Visibility timeout (segundos) para las colas del pool video. El worker lo extiende con ChangeMessageVisibility mientras procesa con ffmpeg."
  type        = number
  default     = 900
}

variable "visibility_timeout_audio" {
  description = "Visibility timeout (segundos) para las colas del pool audio."
  type        = number
  default     = 300
}

variable "visibility_timeout_metadatos" {
  description = "Visibility timeout (segundos) para las colas del pool metadatos."
  type        = number
  default     = 120
}

variable "visibility_timeout_resultados" {
  description = "Visibility timeout (segundos) de la cola de resultados."
  type        = number
  default     = 60
}

variable "message_retention_work" {
  description = "Retención (segundos) de las seis colas de trabajo. Default: 4 días."
  type        = number
  default     = 345600 # 4 días
}

variable "message_retention_resultados" {
  description = "Retención (segundos) de la cola de resultados. Default: 4 días."
  type        = number
  default     = 345600 # 4 días
}

variable "message_retention_dlq" {
  description = "Retención (segundos) de la DLQ. Tiene que ser mayor que la de las colas de origen: en colas Standard la expiración en la DLQ cuenta desde el encolado original. Default: 14 días."
  type        = number
  default     = 1209600 # 14 días
}

variable "max_receive_count" {
  description = "Cantidad de entregas antes de redirigir el mensaje a la DLQ."
  type        = number
  default     = 3
}
