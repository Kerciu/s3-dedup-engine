"""Minimal gRPC SimilarityService stub for local gateway development."""

from concurrent import futures
import logging

import grpc

from const import GRPC_PORT, MOCK_SIMILARITY_SCORE
from pb import dedup_pb2, dedup_pb2_grpc


class SimilarityService(dedup_pb2_grpc.SimilarityServiceServicer):
    """Returns a fixed low score so mock tickets take the AcceptFull path."""

    def CheckSimilarity(self, request, context):
        logging.info("CheckSimilarity text_len=%d", len(request.text))
        return dedup_pb2.SimilarityResponse(score=MOCK_SIMILARITY_SCORE)


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
