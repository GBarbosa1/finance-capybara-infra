FROM public.ecr.aws/lambda/python:3.13-arm64

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY handler.py market_data.py pivot.py clients.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.lambda_handler"]
