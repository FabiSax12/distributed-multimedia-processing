# Todo en la VPC por defecto y sus subredes públicas por defecto.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_region" "current" {}

# AMI de Amazon Linux 2023 x86_64, resuelta desde el parámetro SSM público
# (nunca hardcodeada).
data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  ami_id     = data.aws_ssm_parameter.al2023_ami.value
  subnet_ids = data.aws_subnets.default.ids

  # Nombres lógicos de las seis colas de trabajo (deben coincidir con
  # modules/queues y shared/routing.py).
  work_queue_names = [
    "video-alta", "video-normal",
    "audio-alta", "audio-normal",
    "metadatos-alta", "metadatos-normal",
  ]

  common_env_vars = {
    aws_region      = data.aws_region.current.region
    queue_urls_json = jsonencode(var.queue_urls)
    table_cases     = var.table_cases_name
    table_subtasks  = var.table_subtasks_name
    table_workers   = var.table_workers_name
    dataset_bucket  = var.dataset_bucket_name
    results_bucket  = var.results_bucket_name
  }
}

# ---------------------------------------------------------------------------
# Coordinador: 1 instancia, t3.small, con Elastic IP.
# ---------------------------------------------------------------------------

resource "aws_instance" "coordinator" {
  ami                         = local.ami_id
  instance_type               = var.coordinator_instance_type
  subnet_id                   = local.subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.coordinator.id]
  iam_instance_profile        = aws_iam_instance_profile.coordinator.name
  associate_public_ip_address = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.coordinator_root_volume_size
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/coordinator.sh.tftpl", merge(local.common_env_vars, {
    api_port             = var.api_port
    origin_verify_secret = var.origin_verify_secret
  }))

  tags = {
    Name = "${var.prefix}-${var.env}-coordinator"
    Role = "coordinator"
  }
}

resource "aws_eip" "coordinator" {
  instance = aws_instance.coordinator.id
  domain   = "vpc"

  tags = {
    Name = "${var.prefix}-${var.env}-coordinator-eip"
  }
}

# ---------------------------------------------------------------------------
# Workers: un pool por tipo de medio. Escalar = cambiar var.worker_count.*
# ---------------------------------------------------------------------------

resource "aws_instance" "worker_video" {
  count = var.worker_count.video

  ami                         = local.ami_id
  instance_type               = var.worker_video_instance_type
  subnet_id                   = element(local.subnet_ids, count.index % length(local.subnet_ids))
  vpc_security_group_ids      = [aws_security_group.workers.id]
  iam_instance_profile        = aws_iam_instance_profile.worker.name
  associate_public_ip_address = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.worker_video_root_volume_size
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/worker.sh.tftpl", merge(local.common_env_vars, {
    pool           = "video"
    install_ffmpeg = true
  }))

  tags = {
    Name = "${var.prefix}-${var.env}-worker-video-${count.index}"
    Role = "worker"
    Pool = "video"
  }
}

resource "aws_instance" "worker_audio" {
  count = var.worker_count.audio

  ami                         = local.ami_id
  instance_type               = var.worker_audio_instance_type
  subnet_id                   = element(local.subnet_ids, count.index % length(local.subnet_ids))
  vpc_security_group_ids      = [aws_security_group.workers.id]
  iam_instance_profile        = aws_iam_instance_profile.worker.name
  associate_public_ip_address = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.worker_audio_root_volume_size
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/worker.sh.tftpl", merge(local.common_env_vars, {
    pool           = "audio"
    install_ffmpeg = true
  }))

  tags = {
    Name = "${var.prefix}-${var.env}-worker-audio-${count.index}"
    Role = "worker"
    Pool = "audio"
  }
}

resource "aws_instance" "worker_metadatos" {
  count = var.worker_count.metadatos

  ami                         = local.ami_id
  instance_type               = var.worker_metadatos_instance_type
  subnet_id                   = element(local.subnet_ids, count.index % length(local.subnet_ids))
  vpc_security_group_ids      = [aws_security_group.workers.id]
  iam_instance_profile        = aws_iam_instance_profile.worker.name
  associate_public_ip_address = true

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.worker_metadatos_root_volume_size
    encrypted   = true
  }

  user_data = templatefile("${path.module}/templates/worker.sh.tftpl", merge(local.common_env_vars, {
    pool = "metadatos"
    # También necesita ffmpeg: la operación metadata usa ffprobe y POLL_ORDER
    # lo pone a ayudar con audio-normal (audio_convert, image_thumbnail).
    install_ffmpeg = true
  }))

  tags = {
    Name = "${var.prefix}-${var.env}-worker-metadatos-${count.index}"
    Role = "worker"
    Pool = "metadatos"
  }
}
