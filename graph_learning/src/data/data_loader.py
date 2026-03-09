"""
FlowsheetDataLoader: Load and parse JSON flowsheet files
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FlowsheetDataLoader:
    """Loads flowsheet data from JSON files"""
    
    def __init__(self, data_dir: str):
        """
        Initialize the data loader.
        
        Args:
            data_dir: Path to directory containing flowsheet JSON files
        """
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise ValueError(f"Data directory {data_dir} does not exist")
        
        self.flowsheets = []
        self.metadata = []
        
    def load_all_flowsheets(self) -> List[Dict[str, Any]]:
        """
        Load all JSON flowsheet files from the data directory.
        
        Returns:
            List of flowsheet dictionaries
        """
        json_files = list(self.data_dir.glob("*.json"))
        
        if not json_files:
            raise ValueError(f"No JSON files found in {self.data_dir}")
        
        logger.info(f"Found {len(json_files)} flowsheet files")
        
        for json_file in json_files:
            try:
                flowsheet = self.load_flowsheet(json_file)
                self.flowsheets.append(flowsheet)
                logger.info(f"Loaded: {json_file.name}")
            except Exception as e:
                logger.error(f"Error loading {json_file.name}: {str(e)}")
                continue
        
        logger.info(f"Successfully loaded {len(self.flowsheets)} flowsheets")
        return self.flowsheets
    
    def load_flowsheet(self, file_path: Path) -> Dict[str, Any]:
        """
        Load a single flowsheet JSON file.
        
        Args:
            file_path: Path to JSON file
            
        Returns:
            Dictionary containing flowsheet data
        """
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        # Add filename to metadata for tracking
        if 'metadata' in data:
            data['metadata']['filename'] = file_path.name
        else:
            data['metadata'] = {'filename': file_path.name}
        
        return data
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the loaded flowsheets.
        
        Returns:
            Dictionary with dataset statistics
        """
        if not self.flowsheets:
            return {}
        
        stats = {
            'num_flowsheets': len(self.flowsheets),
            'avg_num_units': sum(len(fs.get('units', [])) for fs in self.flowsheets) / len(self.flowsheets),
            'avg_num_streams': sum(len(fs.get('streams', [])) for fs in self.flowsheets) / len(self.flowsheets),
            'min_num_units': min(len(fs.get('units', [])) for fs in self.flowsheets),
            'max_num_units': max(len(fs.get('units', [])) for fs in self.flowsheets),
            'min_num_streams': min(len(fs.get('streams', [])) for fs in self.flowsheets),
            'max_num_streams': max(len(fs.get('streams', [])) for fs in self.flowsheets),
        }
        
        # Get unique unit types
        all_unit_types = set()
        for fs in self.flowsheets:
            for unit in fs.get('units', []):
                all_unit_types.add(unit.get('unit_type', 'Unknown'))
        stats['num_unique_unit_types'] = len(all_unit_types)
        stats['unique_unit_types'] = sorted(list(all_unit_types))
        
        return stats
    
    def extract_metadata(self) -> List[Dict[str, Any]]:
        """
        Extract metadata from all flowsheets.
        
        Returns:
            List of metadata dictionaries
        """
        metadata_list = []
        for flowsheet in self.flowsheets:
            if 'metadata' in flowsheet:
                metadata_list.append(flowsheet['metadata'])
        
        return metadata_list

