import boto3

client = boto3.client(
    "bedrock-runtime",
    region_name="eu-west-2",
)

response = client.converse(
    modelId="anthropic.claude-sonnet-4-6",
    messages=[
        {
            "role": "user",
            "content": [{"text": "Say hello in one sentence."}],
        }
    ],
)

print(response["output"]["message"]["content"][0]["text"])
