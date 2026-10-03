package constants

const (
	DefaultAWSRegion          = "eu-central-1"
	DefaultAWSAccessKeyID     = "test"
	DefaultAWSSecretAccessKey = "test"
	DefaultLocalStackEndpoint = "http://localhost:4566"
	DefaultAIWorkerGRPCAddr   = "localhost:50051"
	DefaultS3Bucket           = "raw-tickets-bucket"
	DefaultDynamoTable        = "ticket-hashes"
	DynamoFileHashAttr        = "FileHash"
	S3PayloadKeyPrefix        = "payloads/"
	S3MetadataKeyPrefix       = "metadata/"
	SimilarityThreshold       = 0.72
	MockInfoGainThreshold     = 0.5
	MockInfoGainScore         = 0.8
	EnvS3EndpointURL          = "S3_ENDPOINT_URL"
	EnvDynamoEndpointURL      = "DYNAMODB_ENDPOINT_URL"
	EnvAWSRegion              = "AWS_REGION"
	EnvAWSAccessKeyID         = "AWS_ACCESS_KEY_ID"
	EnvAWSSecretAccessKey     = "AWS_SECRET_ACCESS_KEY"
	EnvDedupBucket            = "DEDUP_BUCKET"
	EnvMetadataTable          = "METADATA_TABLE"
	EnvAIWorkerGRPCAddr       = "AI_WORKER_GRPC_ADDR"
)
