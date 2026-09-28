output "coordinator_instance_id" {
  value = aws_instance.coordinator.id
}

output "coordinator_public_ip" {
  description = "Elastic IP del coordinador."
  value       = aws_eip.coordinator.public_ip
}

output "coordinator_public_dns" {
  description = "DNS público asociado a la Elastic IP del coordinador, usado como origin custom de CloudFront."
  value       = aws_eip.coordinator.public_dns
}

output "worker_video_instance_ids" {
  value = aws_instance.worker_video[*].id
}

output "worker_audio_instance_ids" {
  value = aws_instance.worker_audio[*].id
}

output "worker_metadatos_instance_ids" {
  value = aws_instance.worker_metadatos[*].id
}

output "coordinator_security_group_id" {
  value = aws_security_group.coordinator.id
}

output "workers_security_group_id" {
  value = aws_security_group.workers.id
}
