## Тестовое задание (команда исследования технологий машинного обучения)

В рамках задания разворачивается Inference-engine vLLM с использованием Kubernetes. Локальная проверка осуществляется через Minikube

### Предварительная настройка

Перед запуском Minikube я запускал эту команду на увеличение доступной памяти для docker-окружения Minikube, т.к. сталкивался с неизбежной нехваткой памяти после компиляции модели и загрузкой всего ЦПУ под 100% (Не знаю в чем дело)

```bash
docker update --memory=8g --memory-swap=10g minikube
```

Далее запускаем Minikube

```bash
minikube start
```

Еще короче можно через команду

```bash
minikube start --driver=docker --cpus=4 --memory=8g
```

### Docker сборка образов

Нужно собрать два образа

```bash
docker build -t model-downloader:latest downloader/
docker build -t vllm-cpu:latest .
```

Или если процессор на архитектуре Zen 4/5, можно собрать оптимизированный образ, использующий zentorch под капотом 
```bash
docker build --build-arg REQUIREMENTS_FILE=requirements-zen.txt -t vllm-zen:latest .
```

Далее загружаем образы в Minikube

```bash
minikube image load model-downloader:latest

minikube image load vllm-cpu:latest
# Или берем тот что для zen
minikube image load vllm-zen:latest
```

### Запуск кластера

Предварительно нужно задать ключ Hugging Face (опционально) и ключ для vLLM API в .env.secrets

```bash
cp k8s/base/.env.secrets.example k8s/base/.env.secrets
```

Был использован Kustomize, поэтому запуск производиться через команду

```bash
kubectl apply -k k8s/base
# Или берем версию для zen
kubectl apply -k k8s/overlays/zen/
```

Далее ждем пока initContainer загружает модель. Полюбоваться загрузкой можно через логи Kubernetes

```bash
kubectl logs deployment/vllm -c model-downloader -f
```

После загрузки и предварительной подготовки vLLM приложение будет работать в штатном режиме. Статус запуска можно узнать через команду

```bash
kubectl get pods -w
```

Чтобы проверить работу сервиса, можно отправить http запрос. Для этого связываем локальный порт с портом пода

```bash
kubectl port-forward svc/vllm 8000:8000
```

Далее отправляем запрос (не забываем вставить ключ)

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "local-instruct",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ],
    "max_tokens": 32
  }'
```