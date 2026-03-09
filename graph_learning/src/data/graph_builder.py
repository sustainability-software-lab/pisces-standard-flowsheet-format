"""
FlowsheetGraphBuilder: Convert flowsheet data to PyTorch Geometric Data objects
"""

import torch
from torch_geometric.data import Data
from typing import Dict, List, Any, Tuple
import numpy as np
import logging

logger = logging.getLogger(__name__)


class FlowsheetGraphBuilder:
    """Builds PyTorch Geometric graph objects from flowsheet data"""
    
    def __init__(self, feature_extractor):
        """
        Initialize the graph builder.
        
        Args:
            feature_extractor: Fitted FeatureExtractor instance
        """
        self.feature_extractor = feature_extractor
    
    def build_graph(self, flowsheet: Dict[str, Any], target_type: str = 'total_installed_cost') -> Data:
        """
        Convert a flowsheet dictionary to a PyTorch Geometric Data object.
        
        Args:
            flowsheet: Flowsheet dictionary
            target_type: Type of target variable to extract
            
        Returns:
            PyTorch Geometric Data object
        """
        # Extract features
        node_features = self.feature_extractor.extract_node_features(flowsheet)
        edge_features = self.feature_extractor.extract_edge_features(flowsheet)
        target = self.feature_extractor.extract_target(flowsheet, target_type)
        
        # Build edge index (connectivity)
        edge_index, edge_attr = self._build_edge_index(flowsheet, edge_features)
        
        # Convert to PyTorch tensors
        x = torch.tensor(node_features, dtype=torch.float)
        edge_index = torch.tensor(edge_index, dtype=torch.long)
        y = torch.tensor([target], dtype=torch.float)
        
        # Create Data object
        data = Data(
            x=x,
            edge_index=edge_index,
            y=y
        )
        
        # Add edge attributes if available
        if edge_attr is not None and len(edge_attr) > 0:
            data.edge_attr = torch.tensor(edge_attr, dtype=torch.float)
        
        # Note: metadata is NOT added to Data object to avoid batching issues
        # Store it separately if needed for inference/evaluation
        
        return data
    
    def _build_edge_index(self, flowsheet: Dict[str, Any], edge_features: np.ndarray) -> Tuple[List[List[int]], np.ndarray]:
        """
        Build edge index from stream connections.
        
        Args:
            flowsheet: Flowsheet dictionary
            edge_features: Edge feature matrix
            
        Returns:
            Tuple of (edge_index, edge_features) where edge_index is [[sources], [targets]]
        """
        units = flowsheet.get('units', [])
        streams = flowsheet.get('streams', [])
        
        # Create a mapping from unit ID to node index
        unit_id_to_idx = {unit['id']: idx for idx, unit in enumerate(units)}
        
        # Build edge list
        source_nodes = []
        target_nodes = []
        valid_edge_features = []
        
        for idx, stream in enumerate(streams):
            source_id = stream.get('source_unit_id')
            sink_id = stream.get('sink_unit_id')
            
            # Skip streams with no valid source or sink
            if source_id == 'None' or sink_id == 'None':
                continue
            
            # Skip if unit IDs not in our unit list
            if source_id not in unit_id_to_idx or sink_id not in unit_id_to_idx:
                logger.warning(f"Stream references unknown unit: {source_id} -> {sink_id}")
                continue
            
            source_idx = unit_id_to_idx[source_id]
            sink_idx = unit_id_to_idx[sink_id]
            
            source_nodes.append(source_idx)
            target_nodes.append(sink_idx)
            
            # Keep corresponding edge features
            if idx < len(edge_features):
                valid_edge_features.append(edge_features[idx])
        
        edge_index = [source_nodes, target_nodes]
        edge_attr = np.array(valid_edge_features) if valid_edge_features else np.array([])
        
        return edge_index, edge_attr
    
    def build_dataset(self, flowsheets: List[Dict[str, Any]], target_type: str = 'total_installed_cost') -> List[Data]:
        """
        Build a dataset of PyG Data objects from multiple flowsheets.
        
        Args:
            flowsheets: List of flowsheet dictionaries
            target_type: Type of target variable to extract
            
        Returns:
            List of PyTorch Geometric Data objects
        """
        dataset = []
        
        for i, flowsheet in enumerate(flowsheets):
            try:
                data = self.build_graph(flowsheet, target_type)
                dataset.append(data)
            except Exception as e:
                logger.error(f"Error building graph for flowsheet {i}: {str(e)}")
                continue
        
        logger.info(f"Built dataset with {len(dataset)} graphs")
        return dataset
    
    def get_dataset_statistics(self, dataset: List[Data]) -> Dict[str, Any]:
        """
        Get statistics about the dataset.
        
        Args:
            dataset: List of Data objects
            
        Returns:
            Dictionary with dataset statistics
        """
        if not dataset:
            return {}
        
        num_nodes_list = [data.num_nodes for data in dataset]
        num_edges_list = [data.num_edges for data in dataset]
        targets = [data.y.item() for data in dataset]
        
        stats = {
            'num_graphs': len(dataset),
            'avg_num_nodes': np.mean(num_nodes_list),
            'std_num_nodes': np.std(num_nodes_list),
            'min_num_nodes': np.min(num_nodes_list),
            'max_num_nodes': np.max(num_nodes_list),
            'avg_num_edges': np.mean(num_edges_list),
            'std_num_edges': np.std(num_edges_list),
            'min_num_edges': np.min(num_edges_list),
            'max_num_edges': np.max(num_edges_list),
            'avg_target': np.mean(targets),
            'std_target': np.std(targets),
            'min_target': np.min(targets),
            'max_target': np.max(targets),
            'num_node_features': dataset[0].num_node_features,
            'num_edge_features': dataset[0].num_edge_features if dataset[0].edge_attr is not None else 0,
        }
        
        return stats

