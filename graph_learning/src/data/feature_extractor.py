"""
FeatureExtractor: Extract and normalize features from flowsheet data
"""

import numpy as np
from typing import Dict, List, Any, Tuple, Optional
from sklearn.preprocessing import StandardScaler, LabelEncoder
import logging

logger = logging.getLogger(__name__)


class FeatureExtractor:
    """Extracts and normalizes features from flowsheet data"""
    
    def __init__(self):
        self.unit_type_encoder = LabelEncoder()
        self.node_scaler = StandardScaler()
        self.edge_scaler = StandardScaler()
        
        self.fitted = False
        self.unit_types_seen = set()
        
    def fit(self, flowsheets: List[Dict[str, Any]]):
        """
        Fit the encoders and scalers on the training data.
        
        Args:
            flowsheets: List of flowsheet dictionaries
        """
        # Collect all unit types
        all_unit_types = []
        all_node_features = []
        all_edge_features = []
        
        for flowsheet in flowsheets:
            # Collect unit types
            for unit in flowsheet.get('units', []):
                unit_type = unit.get('unit_type', 'Unknown')
                all_unit_types.append(unit_type)
                self.unit_types_seen.add(unit_type)
            
            # Collect node features for fitting scaler
            node_feats = self._extract_raw_node_features(flowsheet)
            all_node_features.extend(node_feats)
            
            # Collect edge features for fitting scaler
            edge_feats = self._extract_raw_edge_features(flowsheet)
            all_edge_features.extend(edge_feats)
        
        # Fit encoders
        self.unit_type_encoder.fit(all_unit_types)
        
        # Fit scalers
        if all_node_features:
            self.node_scaler.fit(all_node_features)
        if all_edge_features:
            self.edge_scaler.fit(all_edge_features)
        
        self.fitted = True
        logger.info(f"Fitted feature extractor on {len(flowsheets)} flowsheets")
        logger.info(f"Found {len(self.unit_types_seen)} unique unit types")
    
    def _extract_raw_node_features(self, flowsheet: Dict[str, Any]) -> List[List[float]]:
        """Extract raw node features before normalization"""
        features = []
        
        for unit in flowsheet.get('units', []):
            # Extract numerical features
            unit_features = self._get_unit_features(unit)
            features.append(unit_features)
        
        return features
    
    def _extract_raw_edge_features(self, flowsheet: Dict[str, Any]) -> List[List[float]]:
        """Extract raw edge features before normalization"""
        features = []
        
        for stream in flowsheet.get('streams', []):
            stream_features = self._get_stream_features(stream)
            features.append(stream_features)
        
        return features
    
    def _get_unit_features(self, unit: Dict[str, Any]) -> List[float]:
        """
        Extract numerical features from a unit operation.
        
        Features include:
        - Installed cost
        - Purchase cost
        - Power consumption
        - Heat duty (sum of heating/cooling)
        """
        installed_cost = sum(unit.get('installed_costs', {}).values())
        purchase_cost = sum(unit.get('purchase_costs', {}).values())
        
        utility_consumption = unit.get('utility_consumption_results', {})
        power_consumption = utility_consumption.get('Marginal grid electricity', 0.0)
        
        # Sum all heat duties
        heat_duty = sum(v for k, v in utility_consumption.items() 
                       if k != 'Marginal grid electricity')
        
        # Get design results (if available)
        design_results = unit.get('design_results', {})
        flow_rate = design_results.get('Flow rate', 0.0)
        
        return [
            installed_cost,
            purchase_cost,
            power_consumption,
            abs(heat_duty),
            flow_rate
        ]
    
    def _get_stream_features(self, stream: Dict[str, Any]) -> List[float]:
        """
        Extract numerical features from a stream.
        
        Features include:
        - Mass flow rate
        - Molar flow rate
        - Volumetric flow rate
        - Temperature
        - Pressure
        - Price
        """
        props = stream.get('stream_properties', {})
        
        mass_flow = props.get('total_mass_flow', {}).get('value', 0.0)
        molar_flow = props.get('total_molar_flow', {}).get('value', 0.0)
        vol_flow = props.get('total_volumetric_flow', {}).get('value', 0.0)
        temperature = props.get('temperature', {}).get('value', 298.15)  # Default 25C
        pressure = props.get('pressure', {}).get('value', 101325.0)  # Default 1 atm
        price = stream.get('price', {}).get('value', 0.0)
        
        return [
            mass_flow,
            molar_flow,
            vol_flow,
            temperature,
            pressure,
            price
        ]
    
    def extract_node_features(self, flowsheet: Dict[str, Any]) -> np.ndarray:
        """
        Extract and normalize node features.
        
        Args:
            flowsheet: Flowsheet dictionary
            
        Returns:
            Normalized node feature matrix (num_nodes, num_features)
        """
        if not self.fitted:
            raise ValueError("FeatureExtractor must be fitted before extraction")
        
        units = flowsheet.get('units', [])
        
        # Extract unit type encodings
        unit_types = []
        for unit in units:
            unit_type = unit.get('unit_type', 'Unknown')
            # Handle unseen unit types
            if unit_type not in self.unit_types_seen:
                logger.warning(f"Unseen unit type: {unit_type}, using 'Unknown'")
                unit_type = 'Unknown'
            unit_types.append(unit_type)
        
        unit_type_ids = self.unit_type_encoder.transform(unit_types).reshape(-1, 1)
        
        # Extract numerical features
        raw_features = self._extract_raw_node_features(flowsheet)
        normalized_features = self.node_scaler.transform(raw_features)
        
        # Combine unit type with normalized features
        node_features = np.hstack([unit_type_ids, normalized_features])
        
        return node_features.astype(np.float32)
    
    def extract_edge_features(self, flowsheet: Dict[str, Any]) -> np.ndarray:
        """
        Extract and normalize edge features.
        
        Args:
            flowsheet: Flowsheet dictionary
            
        Returns:
            Normalized edge feature matrix (num_edges, num_features)
        """
        if not self.fitted:
            raise ValueError("FeatureExtractor must be fitted before extraction")
        
        raw_features = self._extract_raw_edge_features(flowsheet)
        
        if not raw_features:
            return np.array([]).reshape(0, 6).astype(np.float32)
        
        normalized_features = self.edge_scaler.transform(raw_features)
        
        return normalized_features.astype(np.float32)
    
    def extract_target(self, flowsheet: Dict[str, Any], target_type: str = 'total_installed_cost') -> float:
        """
        Extract target variable from flowsheet.
        
        Args:
            flowsheet: Flowsheet dictionary
            target_type: Type of target ('total_installed_cost', 'total_purchase_cost', etc.)
            
        Returns:
            Target value
        """
        if target_type == 'total_installed_cost':
            total = 0.0
            for unit in flowsheet.get('units', []):
                total += sum(unit.get('installed_costs', {}).values())
            return total
        
        elif target_type == 'total_purchase_cost':
            total = 0.0
            for unit in flowsheet.get('units', []):
                total += sum(unit.get('purchase_costs', {}).values())
            return total
        
        elif target_type == 'total_power_consumption':
            total = 0.0
            for unit in flowsheet.get('units', []):
                utility_consumption = unit.get('utility_consumption_results', {})
                total += utility_consumption.get('Marginal grid electricity', 0.0)
            return total
        
        else:
            raise ValueError(f"Unknown target type: {target_type}")

