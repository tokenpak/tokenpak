# Deployment: AWS ECS Fargate

Deploy TokenPak on AWS ECS with Fargate (serverless, no EC2 management).

## Quick Reference

```bash
# 1. Push image to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com
docker build -t tokenpak .
docker tag tokenpak ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/tokenpak:latest
docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/tokenpak:latest

# 2. Store API keys in Secrets Manager
aws secretsmanager create-secret --name tokenpak/anthropic-api-key --secret-string "sk-ant-..."

# 3. Create CloudWatch log group
aws logs create-log-group --log-group-name /ecs/tokenpak

# 4. Register task definition (edit ACCOUNT_ID first)
aws ecs register-task-definition --cli-input-json file://deployments/aws-ecs/task-definition.json

# 5. Deploy service
aws ecs create-service \
  --cluster tokenpak-cluster \
  --service-name tokenpak \
  --task-definition tokenpak \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"
```

## View Logs

```bash
aws logs tail /ecs/tokenpak --follow
```

## Update Image

```bash
aws ecs update-service --cluster tokenpak-cluster --service tokenpak --force-new-deployment
```

## Deployment Files

See `deployments/aws-ecs/task-definition.json` for the full ECS task definition.
