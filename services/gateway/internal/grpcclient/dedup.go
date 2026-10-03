package grpcclient

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"s3-dedup-engine/services/gateway/internal/constants"
	pb "s3-dedup-engine/services/gateway/pb"
)

// DedupResult is the terminal deduplication decision returned by the AI worker.
type DedupResult struct {
	Status           string
	QualityScore     float32
	Distance         float32
	ExistingImageKey string
}

// DedupClient streams image bytes to the AI worker ProcessImageStream RPC.
type DedupClient struct {
	conn   *grpc.ClientConn
	client pb.ImageDedupServiceClient
}

// DialDedup connects to the AI worker address resolved from the environment.
func DialDedup() (*DedupClient, error) {
	addr := workerAddr()
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("dial ai worker at %s: %w", addr, err)
	}
	slog.Debug("connected to ai worker", "addr", addr)
	return &DedupClient{conn: conn, client: pb.NewImageDedupServiceClient(conn)}, nil
}

// ProcessImage streams body in fixed-size chunks and awaits the worker decision.
func (c *DedupClient) ProcessImage(ctx context.Context, imageKey string, totalSize int64, body io.Reader) (DedupResult, error) {
	stream, err := c.client.ProcessImageStream(ctx)
	if err != nil {
		return DedupResult{}, fmt.Errorf("open process image stream: %w", err)
	}

	if err := sendChunks(stream, imageKey, totalSize, body); err != nil {
		return DedupResult{}, err
	}

	resp, err := stream.CloseAndRecv()
	if err != nil {
		return DedupResult{}, fmt.Errorf("await process image response: %w", err)
	}

	return DedupResult{
		Status:           resp.GetStatus(),
		QualityScore:     resp.GetQualityScore(),
		Distance:         resp.GetDistance(),
		ExistingImageKey: resp.GetExistingImageKey(),
	}, nil
}

// Close releases the underlying gRPC connection.
func (c *DedupClient) Close() error {
	if c.conn == nil {
		return nil
	}
	return c.conn.Close()
}

func sendChunks(stream pb.ImageDedupService_ProcessImageStreamClient, imageKey string, totalSize int64, body io.Reader) error {
	buf := make([]byte, constants.StreamChunkBytes)
	var sent int

	for {
		n, readErr := body.Read(buf)
		if n > 0 {
			if err := stream.Send(buildChunk(buf[:n], imageKey, totalSize, sent)); err != nil {
				if errors.Is(err, io.EOF) {
					slog.Debug("ai worker closed the stream early", "image_key", imageKey, "chunks", sent)
					return nil
				}
				return fmt.Errorf("send image chunk %d: %w", sent, err)
			}
			sent++
		}
		if errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil {
			return fmt.Errorf("read image for streaming: %w", readErr)
		}
	}

	if sent == 0 {
		return fmt.Errorf("image %s produced no bytes to stream", imageKey)
	}
	slog.Debug("streamed image to ai worker",
		"image_key", imageKey,
		"chunks", sent,
		"bytes", totalSize,
	)
	return nil
}

func buildChunk(data []byte, imageKey string, totalSize int64, index int) *pb.ImageChunk {
	chunk := &pb.ImageChunk{Data: data}
	if index == 0 {
		chunk.ImageKey = imageKey
		chunk.TotalSize = totalSize
	}
	return chunk
}

func workerAddr() string {
	if addr := os.Getenv(constants.EnvAIWorkerGRPCAddr); addr != "" {
		return addr
	}
	return constants.DefaultAIWorkerGRPCAddr
}
