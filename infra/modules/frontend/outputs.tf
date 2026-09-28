output "site_bucket_name" {
  value = aws_s3_bucket.site.bucket
}

output "site_bucket_id" {
  value = aws_s3_bucket.site.id
}

output "distribution_id" {
  value = aws_cloudfront_distribution.this.id
}

output "cloudfront_domain_name" {
  description = "Dominio *.cloudfront.net de la distribución."
  value       = aws_cloudfront_distribution.this.domain_name
}
