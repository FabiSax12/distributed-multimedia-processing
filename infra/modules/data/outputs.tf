output "cases_table_name" {
  value = aws_dynamodb_table.cases.name
}

output "cases_table_arn" {
  value = aws_dynamodb_table.cases.arn
}

output "subtasks_table_name" {
  value = aws_dynamodb_table.subtasks.name
}

output "subtasks_table_arn" {
  value = aws_dynamodb_table.subtasks.arn
}

output "workers_table_name" {
  value = aws_dynamodb_table.workers.name
}

output "workers_table_arn" {
  value = aws_dynamodb_table.workers.arn
}

output "dataset_bucket_id" {
  value = aws_s3_bucket.dataset.id
}

output "dataset_bucket_name" {
  value = aws_s3_bucket.dataset.bucket
}

output "dataset_bucket_arn" {
  value = aws_s3_bucket.dataset.arn
}

output "results_bucket_id" {
  value = aws_s3_bucket.results.id
}

output "results_bucket_name" {
  value = aws_s3_bucket.results.bucket
}

output "results_bucket_arn" {
  value = aws_s3_bucket.results.arn
}
