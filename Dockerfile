FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir --no-deps cordon

RUN pip install --no-cache-dir \
    numpy litellm tqdm tokenizers boto3 genji

COPY src/flare/ ${LAMBDA_TASK_ROOT}/flare/

CMD ["flare.handler.handler"]
