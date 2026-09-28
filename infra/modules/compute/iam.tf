# Dos roles de instancia con mínimo privilegio (coordinador y worker), en
# lugar de uno compartido. Acceso por SSM Session Manager: sin key pair ni
# puerto 22, con AmazonSSMManagedInstanceCore adjunto a ambos.

data "aws_iam_policy_document" "assume_role_ec2" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------------------
# Rol coordinador
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "coordinator" {
  statement {
    sid       = "SqsWorkQueuesSend"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [for k in local.work_queue_names : var.queue_arns[k]]
  }

  statement {
    sid = "SqsResultadosDlqConsume"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [var.queue_arns["resultados"], var.queue_arns["dlq"]]
  }

  statement {
    sid = "DynamoDbCasesSubtasksReadWrite"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:TransactGetItems",
      "dynamodb:TransactWriteItems",
    ]
    resources = [var.table_cases_arn, var.table_subtasks_arn]
  }

  statement {
    sid       = "DynamoDbWorkersRead"
    actions   = ["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:Query"]
    resources = [var.table_workers_arn]
  }

  statement {
    sid       = "S3DatasetList"
    actions   = ["s3:ListBucket"]
    resources = [var.dataset_bucket_arn]
  }

  statement {
    sid       = "S3DatasetUploadsPresign"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.dataset_bucket_arn}/uploads/*"]
  }

  statement {
    sid       = "S3ResultsList"
    actions   = ["s3:ListBucket"]
    resources = [var.results_bucket_arn]
  }

  statement {
    sid       = "S3ResultsReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.results_bucket_arn}/*"]
  }
}

resource "aws_iam_role" "coordinator" {
  name               = "${var.prefix}-${var.env}-coordinator"
  assume_role_policy = data.aws_iam_policy_document.assume_role_ec2.json
}

resource "aws_iam_role_policy" "coordinator" {
  name   = "${var.prefix}-${var.env}-coordinator"
  role   = aws_iam_role.coordinator.id
  policy = data.aws_iam_policy_document.coordinator.json
}

resource "aws_iam_role_policy_attachment" "coordinator_ssm" {
  role       = aws_iam_role.coordinator.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "coordinator" {
  name = "${var.prefix}-${var.env}-coordinator"
  role = aws_iam_role.coordinator.name
}

# ---------------------------------------------------------------------------
# Rol worker (compartido por los pools video/audio/metadatos)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "worker" {
  statement {
    sid = "SqsWorkQueuesConsume"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [for k in local.work_queue_names : var.queue_arns[k]]
  }

  statement {
    sid       = "SqsResultadosSend"
    actions   = ["sqs:SendMessage"]
    resources = [var.queue_arns["resultados"]]
  }

  statement {
    sid       = "DynamoDbWorkersHeartbeat"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [var.table_workers_arn]
  }

  statement {
    sid       = "DynamoDbCasesReadForCancellation"
    actions   = ["dynamodb:GetItem"]
    resources = [var.table_cases_arn]
  }

  statement {
    sid       = "S3DatasetRead"
    actions   = ["s3:GetObject"]
    resources = ["${var.dataset_bucket_arn}/*"]
  }

  statement {
    sid       = "S3ResultsWrite"
    actions   = ["s3:PutObject"]
    resources = ["${var.results_bucket_arn}/results/*"]
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.prefix}-${var.env}-worker"
  assume_role_policy = data.aws_iam_policy_document.assume_role_ec2.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "${var.prefix}-${var.env}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_role_policy_attachment" "worker_ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.prefix}-${var.env}-worker"
  role = aws_iam_role.worker.name
}
