"""
GeminiExplainer: Use Google's Gemini API to generate natural language explanations
"""

import os
from typing import Dict, Any, Optional
import logging
import json

logger = logging.getLogger(__name__)

try:
    from google.cloud import aiplatform
    from vertexai.preview.generative_models import GenerativeModel
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("Google Cloud AI Platform not available. Install with: pip install google-cloud-aiplatform")


class GeminiExplainer:
    """Generate natural language explanations using Google's Gemini API"""
    
    def __init__(
        self,
        project_id: Optional[str] = None,
        location: str = "us-central1",
        model_name: str = "gemini-1.5-pro"
    ):
        """
        Initialize Gemini explainer.
        
        Args:
            project_id: Google Cloud project ID (defaults to env variable)
            location: Google Cloud region
            model_name: Gemini model to use
        """
        if not GEMINI_AVAILABLE:
            raise ImportError("Google Cloud AI Platform is required. Install with: pip install google-cloud-aiplatform")
        
        self.project_id = project_id or os.getenv('GOOGLE_CLOUD_PROJECT')
        self.location = location
        self.model_name = model_name
        
        if not self.project_id:
            logger.warning("No project_id provided. Set GOOGLE_CLOUD_PROJECT environment variable.")
        
        # Initialize Vertex AI
        try:
            aiplatform.init(project=self.project_id, location=self.location)
            self.model = GenerativeModel(model_name)
            logger.info(f"Initialized Gemini model: {model_name}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini: {str(e)}")
            self.model = None
    
    def explain_prediction(
        self,
        prediction_data: Dict[str, Any],
        explanation_data: Optional[Dict[str, Any]] = None,
        audience: str = "technical"
    ) -> str:
        """
        Generate natural language explanation for a prediction.
        
        Args:
            prediction_data: Dictionary with prediction results
            explanation_data: Optional additional context
            audience: Target audience ("technical", "manager", "executive")
            
        Returns:
            Natural language explanation
        """
        if self.model is None:
            return "Gemini model not initialized. Check your Google Cloud credentials."
        
        # Build prompt based on audience
        prompt = self._build_explanation_prompt(
            prediction_data, explanation_data, audience
        )
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Error generating explanation: {str(e)}")
            return f"Error generating explanation: {str(e)}"
    
    def _build_explanation_prompt(
        self,
        prediction_data: Dict[str, Any],
        explanation_data: Optional[Dict[str, Any]],
        audience: str
    ) -> str:
        """Build the prompt for Gemini"""
        
        # Extract key information
        prediction = prediction_data.get('prediction', 'N/A')
        ground_truth = prediction_data.get('ground_truth')
        error = prediction_data.get('absolute_error')
        relative_error = prediction_data.get('relative_error')
        
        # Format data for prompt
        data_summary = f"Predicted Total Installed Cost: ${prediction:,.2f}"
        
        if ground_truth is not None:
            data_summary += f"\nActual Total Installed Cost: ${ground_truth:,.2f}"
            data_summary += f"\nAbsolute Error: ${error:,.2f}"
            data_summary += f"\nRelative Error: {relative_error:.2f}%"
        
        # Add process information
        if 'metadata' in prediction_data:
            metadata = prediction_data['metadata']
            data_summary += f"\n\nProcess Information:"
            if 'product_name' in metadata:
                data_summary += f"\n- Product: {metadata['product_name']}"
            if 'feedstock' in metadata:
                data_summary += f"\n- Feedstock: {metadata['feedstock']}"
            if 'organism' in metadata:
                data_summary += f"\n- Organism: {metadata['organism']}"
        
        # Add important components if available
        if explanation_data and 'important_streams' in explanation_data:
            streams = explanation_data['important_streams'][:3]
            if streams:
                data_summary += f"\n\nMost Important Process Streams (by attention):"
                for i, stream in enumerate(streams, 1):
                    data_summary += f"\n{i}. Unit {stream['source_node']} → Unit {stream['target_node']} (weight: {stream['attention_weight']:.3f})"
        
        if explanation_data and 'high_cost_nodes' in explanation_data:
            nodes = explanation_data['high_cost_nodes'][:3]
            if nodes:
                data_summary += f"\n\nHighest Cost Equipment:"
                for i, node in enumerate(nodes, 1):
                    data_summary += f"\n{i}. Unit {node['node_index']} (cost: ${node['cost']:,.2f})"
        
        # Customize prompt based on audience
        if audience == "executive":
            role = "a business executive who needs high-level insights"
            style = "Focus on business impact, cost drivers, and strategic implications. Avoid technical jargon."
        elif audience == "manager":
            role = "a project manager who needs actionable insights"
            style = "Balance technical accuracy with practical implications. Highlight key decisions and trade-offs."
        else:  # technical
            role = "a chemical engineer or technical specialist"
            style = "Provide detailed technical analysis. Include specific equipment and process considerations."
        
        prompt = f"""You are an expert chemical process engineer analyzing the output of a Graph Neural Network model 
that predicts the total installed cost of chemical production processes.

Your audience is {role}.

{style}

Based on the following model output and process data, provide a clear and insightful summary:

{data_summary}

Please provide:
1. A brief summary of the prediction and its accuracy (if ground truth available)
2. Key insights about the cost drivers in this process
3. Any notable patterns or concerns
4. Recommendations for cost optimization (if applicable)

Keep your response concise (4-6 sentences) and actionable."""
        
        return prompt
    
    def extract_features_from_text(self, text_description: str) -> Dict[str, Any]:
        """
        Use Gemini to extract structured process features from text description.
        
        This is useful for Phase 1 if you have unstructured process descriptions.
        
        Args:
            text_description: Natural language description of a chemical process
            
        Returns:
            Structured dictionary with extracted features
        """
        if self.model is None:
            return {}
        
        prompt = f"""You are an expert chemical process engineer. Analyze the following text and extract 
the process flowsheet information. Identify all unit operations as nodes and all material streams as 
directed edges.

Return the information as a single, valid JSON object with two keys: "nodes" and "edges".

- For each node, include a unique 'id', its 'type', and any numerical 'features' like temperature and pressure.
- For each edge, include the 'source' node id, the 'target' node id, and any numerical 'features' like flow_rate.

Text: "{text_description}"

Return only valid JSON, no additional text."""
        
        try:
            response = self.model.generate_content(prompt)
            # Parse JSON from response
            result = json.loads(response.text)
            return result
        except Exception as e:
            logger.error(f"Error extracting features: {str(e)}")
            return {}
    
    def suggest_improvements(
        self,
        prediction_data: Dict[str, Any],
        explanation_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate suggestions for improving the process or reducing costs.
        
        Args:
            prediction_data: Dictionary with prediction results
            explanation_data: Optional additional context
            
        Returns:
            Natural language suggestions
        """
        if self.model is None:
            return "Gemini model not initialized."
        
        prompt = f"""You are an expert chemical process engineer specializing in process optimization 
and cost reduction.

Based on the following chemical process analysis, suggest specific improvements to reduce the 
total installed cost:

Predicted Total Installed Cost: ${prediction_data.get('prediction', 0):,.2f}
Number of Unit Operations: {prediction_data.get('num_nodes', 'N/A')}
Number of Process Streams: {prediction_data.get('num_edges', 'N/A')}

"""
        
        if explanation_data and 'high_cost_nodes' in explanation_data:
            prompt += "\nHighest Cost Equipment:\n"
            for i, node in enumerate(explanation_data['high_cost_nodes'][:3], 1):
                prompt += f"{i}. Unit {node['node_index']} (${node['cost']:,.2f})\n"
        
        prompt += """
Provide 3-5 specific, actionable recommendations for cost reduction. For each recommendation:
1. Describe the change
2. Explain the expected impact
3. Note any potential trade-offs

Be concise and specific."""
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Error generating suggestions: {str(e)}")
            return f"Error generating suggestions: {str(e)}"

