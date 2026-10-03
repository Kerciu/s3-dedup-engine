"""Minimal gRPC SimilarityService mock for visual deduplication development."""

from concurrent import futures
import logging

import grpc

from const import GRPC_PORT, MOCK_SIMILARITY_SCORE
from pb import dedup_pb2, dedup_pb2_grpc


class SimilarityService(dedup_pb2_grpc.SimilarityServiceServicer):
    """Returns 0.0 without a reference image, otherwise a fixed mock score."""

    def CheckSimilarity(self, request, context):
        if not request.reference_image:
            logging.info(
                "CheckSimilarity file=%s no reference; score=0.0",
                request.file_name,
            )
            return dedup_pb2.SimilarityResponse(similarity_score=0.0)
        logging.info(
            "CheckSimilarity file=%s image_bytes=%d reference_bytes=%d score=%.2f",
            request.file_name,
            len(request.image),
            len(request.reference_image),
            MOCK_SIMILARITY_SCORE,
        )
        return dedup_pb2.SimilarityResponse(similarity_score=MOCK_SIMILARITY_SCORE)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    dedup_pb2_grpc.add_SimilarityServiceServicer_to_server(SimilarityService(), server)
    listen_addr = f"[::]:{GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    server.start()
    logging.info("ai_worker listening on %s", listen_addr)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
