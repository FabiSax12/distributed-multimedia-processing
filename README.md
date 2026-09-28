```
distributed-multimedia-processing/
├── infra/                  # Terraform
│   ├── bootstrap/          # backend remoto: bucket de estado + lock (se aplica una vez)
│   ├── modules/
│   │   ├── queues/         # 6 colas de trabajo + resultados + DLQ (redrive)
│   │   ├── data/           # tablas DynamoDB (Cases, SubTasks, Workers) + buckets S3
│   │   ├── compute/        # EC2 coordinador + 3 workers, security groups, rol IAM
│   │   └── frontend/       # bucket del sitio + CloudFront (/api/* → coordinador)
│   └── envs/dev/           # main.tf que compone los módulos, variables, outputs
│
├── shared/                 # Contratos
│   ├── messages.py         # mensaje de sub-tarea y mensaje de resultado
│   ├── models.py           # ítems Case, SubTask, Worker
│   ├── states.py           # estados y su rango (para rechazar mensajes fuera de orden)
│   └── routing.py          # tabla tipo de archivo → operación(es) → cola
│
├── coordinator/
│   ├── api/                # REST: casos, URLs prefirmadas, cancelar, /api/state
│   ├── routing/            # descomposición del caso en sub-tareas + outbox
│   ├── barrier/            # escritura atómica, cierre idempotente, barrido al arrancar
│   ├── consumers/          # hilos: cola de resultados y DLQ
│   ├── reports/            # reporte consolidado por caso
│   └── monitor/            # profundidad de colas, muestras de carga, reglas de balanceo
│
├── workers/                # un solo código, el pool se elige por variable de entorno
│   ├── runner/             # consumo por prioridad, extensión de visibilidad, heartbeat
│   └── ops/                # video.py, audio.py, image.py, metadata.py (ffmpeg / APIs)
│
├── dashboard/              # SPA estática (polling a /api/state)
├── client/                 # generador de carga: arma casos por carpeta/JSON y los envía
├── dataset/                # scripts para armar el dataset, metadatos JSON y subirlo a S3
├── deploy/                 # user-data / systemd de las EC2, script para publicar el dashboard
├── tests/                  # pruebas del barrier y del routing contra DynamoDB/SQS reales
└── docs/
    ├── diagrams/           # los 4 diagramas (.drawio + PNG)
    ├── adr/                # decisiones: pull vs asignación, pools especializados, un solo escritor…
    ├── manual-usuario.md
    └── informe-pruebas/
```