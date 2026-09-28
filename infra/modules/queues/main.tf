# Nombres lógicos exactos, tienen que coincidir con shared/routing.py
# (queue_key(pool, priority) y ALL_WORK_QUEUES).
locals {
  work_queues = {
    "video-alta"       = { visibility_timeout_seconds = var.visibility_timeout_video }
    "video-normal"     = { visibility_timeout_seconds = var.visibility_timeout_video }
    "audio-alta"       = { visibility_timeout_seconds = var.visibility_timeout_audio }
    "audio-normal"     = { visibility_timeout_seconds = var.visibility_timeout_audio }
    "metadatos-alta"   = { visibility_timeout_seconds = var.visibility_timeout_metadatos }
    "metadatos-normal" = { visibility_timeout_seconds = var.visibility_timeout_metadatos }
  }

  # ARNs de las seis colas de trabajo, construidos sin depender del recurso
  # (evita el ciclo: la DLQ necesita estos ARNs en su redrive_allow_policy,
  # y las colas de trabajo necesitan el ARN real de la DLQ en su
  # redrive_policy). El formato de ARN de SQS es determinístico.
  work_queue_arns = {
    for k in keys(local.work_queues) :
    k => "arn:aws:sqs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:${var.prefix}-${var.env}-${k}"
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_sqs_queue" "dlq" {
  name = "${var.prefix}-${var.env}-dlq"

  message_retention_seconds = var.message_retention_dlq
  receive_wait_time_seconds = 20
  sqs_managed_sse_enabled   = true

  # Solo las seis colas de trabajo pueden redirigir mensajes acá.
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = values(local.work_queue_arns)
  })
}

resource "aws_sqs_queue" "work" {
  for_each = local.work_queues

  name = "${var.prefix}-${var.env}-${each.key}"

  visibility_timeout_seconds = each.value.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_work
  receive_wait_time_seconds  = 0
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue" "resultados" {
  name = "${var.prefix}-${var.env}-resultados"

  visibility_timeout_seconds = var.visibility_timeout_resultados
  message_retention_seconds  = var.message_retention_resultados
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
}
