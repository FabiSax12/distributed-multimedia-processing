# Prefix list administrada por AWS con los rangos de salida de CloudFront.
# No alcanza sola como control de acceso (cualquier distribución de
# cualquier cuenta sale por esas IPs) por eso se complementa con el header
# X-Origin-Verify que valida el coordinador (ver modules/frontend).
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "coordinator" {
  name_prefix = "${var.prefix}-${var.env}-coordinator-"
  description = "Coordinador: ingress solo API desde CloudFront, sin SSH"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "API desde CloudFront (origin-facing prefix list)"
    from_port       = var.api_port
    to_port         = var.api_port
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id]
  }

  egress {
    description = "Egress abierto"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.prefix}-${var.env}-coordinator-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "workers" {
  name_prefix = "${var.prefix}-${var.env}-workers-"
  description = "Workers (video/audio/metadatos): sin ingress, egress abierto para SQS/S3/DynamoDB/APIs externas"
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Egress abierto"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.prefix}-${var.env}-workers-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}
