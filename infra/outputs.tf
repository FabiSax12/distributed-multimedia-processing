output "cloudfront_url" {
  description = "URL de CloudFront del dashboard."
  value       = "https://${module.frontend.cloudfront_domain_name}"
}

output "coordinator_eip" {
  description = "Elastic IP del coordinador."
  value       = module.compute.coordinator_public_ip
}

output "instance_ids" {
  description = "IDs de instancia por rol y pool."
  value = {
    coordinator      = module.compute.coordinator_instance_id
    worker_video     = module.compute.worker_video_instance_ids
    worker_audio     = module.compute.worker_audio_instance_ids
    worker_metadatos = module.compute.worker_metadatos_instance_ids
  }
}

output "queue_urls" {
  description = "Mapa nombre lógico -> URL de las ocho colas SQS."
  value       = module.queues.queue_urls
}

output "table_names" {
  description = "Nombres de las tres tablas DynamoDB."
  value = {
    cases    = module.data.cases_table_name
    subtasks = module.data.subtasks_table_name
    workers  = module.data.workers_table_name
  }
}

output "bucket_names" {
  description = "Nombres de los tres buckets S3."
  value = {
    dataset = module.data.dataset_bucket_name
    results = module.data.results_bucket_name
    site    = module.frontend.site_bucket_name
  }
}

output "distribution_id" {
  description = "ID de la distribución de CloudFront."
  value       = module.frontend.distribution_id
}

output "origin_verify_secret" {
  description = "Secreto del header X-Origin-Verify."
  value       = random_password.origin_verify_secret.result
  sensitive   = true
}
