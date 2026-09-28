output "queue_urls" {
  description = "Mapa nombre lógico -> URL, para las ocho colas (seis de trabajo + resultados + dlq)."
  value = merge(
    { for k, q in aws_sqs_queue.work : k => q.id },
    {
      resultados = aws_sqs_queue.resultados.id
      dlq        = aws_sqs_queue.dlq.id
    }
  )
}

output "queue_arns" {
  description = "Mapa nombre lógico -> ARN, para las ocho colas (seis de trabajo + resultados + dlq)."
  value = merge(
    { for k, q in aws_sqs_queue.work : k => q.arn },
    {
      resultados = aws_sqs_queue.resultados.arn
      dlq        = aws_sqs_queue.dlq.arn
    }
  )
}

output "work_queue_names" {
  description = "Nombres lógicos de las seis colas de trabajo."
  value       = keys(local.work_queues)
}
