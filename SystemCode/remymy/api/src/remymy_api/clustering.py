from .inference.service import Clusterer
from .repository.recipe_repository import RecipeRepository
from .schemas import ClusteringRequest, ClusteringResponse

def cluster(clusterer: Clusterer, cluster_request: ClusteringRequest, k: int,
            catalog: RecipeRepository) -> ClusteringResponse:
    neighbors = clusterer.cluster(cluster_request.item.title, k, catalog)
    return ClusteringResponse(
        near_items=neighbors
    )
