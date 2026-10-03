package grpcclient

import (
	"context"
	"fmt"
	"os"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"s3-dedup-engine/services/gateway/internal/constants"
	pb "s3-dedup-engine/services/gateway/pb"
)

// SimilarityClient wraps the AI worker CheckSimilarity RPC.
type SimilarityClient struct {
	conn   *grpc.ClientConn
	client pb.SimilarityServiceClient
}

func DialSimilarity(ctx context.Context) (*SimilarityClient, error) {
	_ = ctx
	addr := os.Getenv(constants.EnvAIWorkerGRPCAddr)
	if addr == "" {
		addr = constants.DefaultAIWorkerGRPCAddr
	}

	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("dial ai worker at %s: %w", addr, err)
	}

	return &SimilarityClient{
		conn:   conn,
		client: pb.NewSimilarityServiceClient(conn),
	}, nil
}

func (c *SimilarityClient) CheckSimilarity(ctx context.Context, text string) (float32, error) {
	resp, err := c.client.CheckSimilarity(ctx, &pb.SimilarityRequest{Text: text})
	if err != nil {
		return 0, fmt.Errorf("CheckSimilarity rpc: %w", err)
	}
	return resp.GetScore(), nil
}

func (c *SimilarityClient) Close() error {
	if c.conn == nil {
		return nil
	}
	return c.conn.Close()
}
