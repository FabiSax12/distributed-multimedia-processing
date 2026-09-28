# DynamoDB: las tres tablas, PAY_PER_REQUEST, sin streams.

resource "aws_dynamodb_table" "cases" {
  name         = "${var.prefix}-${var.env}-cases"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "case_id"

  attribute {
    name = "case_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "subtasks" {
  name         = "${var.prefix}-${var.env}-subtasks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "case_id"
  range_key    = "subtask_id"

  attribute {
    name = "case_id"
    type = "S"
  }

  attribute {
    name = "subtask_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "workers" {
  name         = "${var.prefix}-${var.env}-workers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "worker_id"

  attribute {
    name = "worker_id"
    type = "S"
  }
}

# S3: dataset de entrada y repositorio de resultados. Privados, Block Public
# Access completo, cifrado SSE-S3. El bucket del sitio lo crea modules/frontend.
#
# Las reglas CORS de estos dos buckets NO viven acá: dependen del dominio de
# CloudFront (modules/frontend), y el dominio de CloudFront depende del
# bucket del sitio. Para evitar un ciclo entre módulos, esas reglas se
# definen en infra/main.tf, después de instanciar tanto este módulo como
# el de frontend.

resource "aws_s3_bucket" "dataset" {
  bucket = "${var.prefix}-${var.env}-dataset"

  # Proyecto de estudiante: hay que poder destruir todo sin trabas.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "dataset" {
  bucket = aws_s3_bucket.dataset.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dataset" {
  bucket = aws_s3_bucket.dataset.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "dataset" {
  bucket = aws_s3_bucket.dataset.id

  rule {
    id     = "expire-uploads"
    status = "Enabled"

    filter {
      prefix = "uploads/"
    }

    expiration {
      days = var.uploads_expiration_days
    }
  }
}

resource "aws_s3_bucket" "results" {
  bucket = "${var.prefix}-${var.env}-results"

  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}
