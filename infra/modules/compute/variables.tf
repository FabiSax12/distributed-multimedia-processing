variable "prefix" {
  description = "Prefijo para todos los nombres de recursos."
  type        = string
}

variable "env" {
  description = "Nombre del ambiente (dev, staging, etc.)."
  type        = string
}

variable "api_port" {
  description = "Puerto donde escucha la API del coordinador."
  type        = number
  default     = 8000
}

variable "worker_count" {
  description = "Cantidad de instancias por pool de worker. Escalar un pool es solo cambiar el número acá y volver a aplicar."
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

variable "coordinator_instance_type" {
  type    = string
  default = "t3.small"
}

variable "worker_video_instance_type" {
  type    = string
  default = "c7i-flex.large"
}

variable "worker_audio_instance_type" {
  type    = string
  default = "m7i-flex.large"
}

variable "worker_metadatos_instance_type" {
  type    = string
  default = "t3.micro"
}

variable "coordinator_root_volume_size" {
  description = "Tamaño (GB) del volumen raíz gp3 del coordinador."
  type        = number
  default     = 20
}

variable "worker_video_root_volume_size" {
  description = "Tamaño (GB) del volumen raíz gp3 del worker video (archivos temporales de ffmpeg)."
  type        = number
  default     = 30
}

variable "worker_audio_root_volume_size" {
  description = "Tamaño (GB) del volumen raíz gp3 del worker audio."
  type        = number
  default     = 20
}

variable "worker_metadatos_root_volume_size" {
  description = "Tamaño (GB) del volumen raíz gp3 del worker metadatos."
  type        = number
  default     = 10
}

variable "queue_urls" {
  description = "Mapa nombre lógico -> URL de las ocho colas SQS."
  type        = map(string)
}

variable "queue_arns" {
  description = "Mapa nombre lógico -> ARN de las ocho colas SQS."
  type        = map(string)
}

variable "table_cases_name" {
  type = string
}

variable "table_cases_arn" {
  type = string
}

variable "table_subtasks_name" {
  type = string
}

variable "table_subtasks_arn" {
  type = string
}

variable "table_workers_name" {
  type = string
}

variable "table_workers_arn" {
  type = string
}

variable "dataset_bucket_name" {
  type = string
}

variable "dataset_bucket_arn" {
  type = string
}

variable "results_bucket_name" {
  type = string
}

variable "results_bucket_arn" {
  type = string
}

variable "origin_verify_secret" {
  description = "Secreto compartido con CloudFront (header X-Origin-Verify) que valida el coordinador. Generado en infra/main.tf para evitar un ciclo de dependencia entre modules/compute y modules/frontend."
  type        = string
  sensitive   = true
}
