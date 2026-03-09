"""
Main Flowsheet Design Agent

Orchestrates the entire flowsheet design process from requirements gathering
to SFF file generation.
"""

from typing import Dict, List, Any, Optional
import json
from pathlib import Path

from .dialogue_manager import DialogueManager
from .knowledge_base.flowsheet_retriever import FlowsheetRetriever
from .knowledge_base.unit_library import UnitOperationLibrary
from .tools.requirement_extractor import RequirementExtractor
from .tools.structure_generator import StructureGenerator
from .tools.sff_generator import SFFGenerator
from .tools.design_validator import DesignValidator


class FlowsheetDesignAgent:
    """
    AI Agent for automated flowsheet design.
    
    Capabilities:
    - Interactive requirement gathering
    - Similarity-based knowledge retrieval
    - Structure generation using GNN models
    - Unit configuration and stream calculation
    - SFF file generation and validation
    """
    
    def __init__(
        self,
        knowledge_base_path: str = "data/knowledge_base",
        model_path: str = "models/graph_vae.pth",
        schema_path: str = "schema/sff_schema.json",
        llm_model: str = "gpt-4",
        llm_api_key: Optional[str] = None
    ):
        """
        Initialize the flowsheet design agent.
        
        Args:
            knowledge_base_path: Path to indexed flowsheet knowledge base
            model_path: Path to trained GraphVAE model
            schema_path: Path to SFF JSON schema
            llm_model: LLM model to use for dialogue
            llm_api_key: API key for LLM (if required)
        """
        self.knowledge_base_path = Path(knowledge_base_path)
        self.model_path = Path(model_path)
        self.schema_path = Path(schema_path)
        
        # Initialize components
        self.dialogue_manager = DialogueManager(llm_model, llm_api_key)
        self.flowsheet_retriever = FlowsheetRetriever(self.knowledge_base_path)
        self.unit_library = UnitOperationLibrary(self.knowledge_base_path / "units")
        self.requirement_extractor = RequirementExtractor(llm_model, llm_api_key)
        self.structure_generator = StructureGenerator(self.model_path)
        self.sff_generator = SFFGenerator(self.schema_path)
        self.validator = DesignValidator(self.schema_path)
        
        # Conversation state
        self.conversation_history = []
        self.current_requirements = {}
        self.design_in_progress = None
        
    def start_design_session(self) -> str:
        """
        Start a new flowsheet design session.
        
        Returns:
            Initial greeting and prompt
        """
        self.conversation_history = []
        self.current_requirements = {}
        self.design_in_progress = None
        
        greeting = self.dialogue_manager.generate_greeting()
        self.conversation_history.append({
            'role': 'agent',
            'content': greeting
        })
        
        return greeting
    
    def process_user_input(self, user_input: str) -> str:
        """
        Process user input and generate appropriate response.
        
        Args:
            user_input: User's message
            
        Returns:
            Agent's response
        """
        # Add to conversation history
        self.conversation_history.append({
            'role': 'user',
            'content': user_input
        })
        
        # Determine conversation phase
        phase = self.dialogue_manager.determine_phase(
            self.conversation_history,
            self.current_requirements
        )
        
        # Process based on phase
        if phase == 'requirements_gathering':
            response = self._handle_requirements_gathering(user_input)
        
        elif phase == 'clarification':
            response = self._handle_clarification(user_input)
        
        elif phase == 'design_generation':
            response = self._handle_design_generation()
        
        elif phase == 'design_presentation':
            response = self._handle_design_presentation()
        
        elif phase == 'refinement':
            response = self._handle_refinement(user_input)
        
        elif phase == 'finalization':
            response = self._handle_finalization(user_input)
        
        else:
            response = "I'm not sure how to proceed. Could you clarify what you'd like to do?"
        
        # Add response to history
        self.conversation_history.append({
            'role': 'agent',
            'content': response
        })
        
        return response
    
    def _handle_requirements_gathering(self, user_input: str) -> str:
        """Handle initial requirements gathering phase."""
        # Extract structured requirements from user input
        extracted = self.requirement_extractor.extract_requirements(
            user_input,
            self.current_requirements
        )
        
        # Update current requirements
        self.current_requirements.update(extracted)
        
        # Search for similar flowsheets
        similar_flowsheets = []
        if 'product' in self.current_requirements:
            similar_flowsheets = self.flowsheet_retriever.find_similar(
                product=self.current_requirements.get('product'),
                feedstock=self.current_requirements.get('feedstock'),
                scale=self.current_requirements.get('scale'),
                k=3
            )
        
        # Generate response
        response = self.dialogue_manager.generate_requirements_response(
            extracted_info=extracted,
            similar_flowsheets=similar_flowsheets,
            missing_info=extracted.get('missing_info', [])
        )
        
        return response
    
    def _handle_clarification(self, user_input: str) -> str:
        """Handle clarification questions phase."""
        # Extract answers from user input
        answers = self.requirement_extractor.extract_answers(
            user_input,
            self.current_requirements
        )
        
        # Update requirements
        self.current_requirements.update(answers)
        
        # Check if we have enough information
        missing_critical = self._check_missing_critical_info()
        
        if missing_critical:
            # Generate more clarification questions
            response = self.dialogue_manager.generate_clarification_questions(
                missing_info=missing_critical,
                current_requirements=self.current_requirements
            )
        else:
            # Ready to generate design
            response = self.dialogue_manager.generate_ready_to_design_message(
                self.current_requirements
            )
        
        return response
    
    def _handle_design_generation(self) -> str:
        """Handle design generation phase."""
        # Find similar reference flowsheets
        references = self.flowsheet_retriever.find_similar(
            product=self.current_requirements['product'],
            feedstock=self.current_requirements.get('feedstock'),
            scale=self.current_requirements.get('scale'),
            k=5
        )
        
        # Generate flowsheet structure
        structure = self.structure_generator.generate_structure(
            requirements=self.current_requirements,
            reference_flowsheets=references
        )
        
        # Configure units
        configured = self.structure_generator.configure_units(
            structure=structure,
            requirements=self.current_requirements,
            unit_library=self.unit_library
        )
        
        # Calculate streams
        with_streams = self.structure_generator.calculate_streams(
            flowsheet=configured,
            requirements=self.current_requirements
        )
        
        # Validate design
        validation_report = self.validator.validate(with_streams)
        
        # Store design
        self.design_in_progress = {
            'flowsheet': with_streams,
            'validation': validation_report,
            'references': references
        }
        
        # Generate response
        response = self.dialogue_manager.generate_design_complete_message(
            flowsheet=with_streams,
            validation=validation_report
        )
        
        return response
    
    def _handle_design_presentation(self) -> str:
        """Handle design presentation phase."""
        if not self.design_in_progress:
            return "No design available to present."
        
        response = self.dialogue_manager.present_design(
            self.design_in_progress['flowsheet']
        )
        
        return response
    
    def _handle_refinement(self, user_input: str) -> str:
        """Handle iterative design refinement."""
        if not self.design_in_progress:
            return "No design available to refine."
        
        # Parse modification request
        modifications = self.requirement_extractor.extract_modifications(
            user_input,
            self.design_in_progress['flowsheet']
        )
        
        # Apply modifications
        modified = self.structure_generator.apply_modifications(
            flowsheet=self.design_in_progress['flowsheet'],
            modifications=modifications
        )
        
        # Re-validate
        validation = self.validator.validate(modified)
        
        # Update design
        self.design_in_progress['flowsheet'] = modified
        self.design_in_progress['validation'] = validation
        
        # Generate response
        response = self.dialogue_manager.generate_modification_response(
            modifications=modifications,
            validation=validation
        )
        
        return response
    
    def _handle_finalization(self, user_input: str) -> str:
        """Handle design finalization and SFF generation."""
        if not self.design_in_progress:
            return "No design available to finalize."
        
        # Generate SFF file
        sff_data = self.sff_generator.generate(
            flowsheet=self.design_in_progress['flowsheet'],
            requirements=self.current_requirements
        )
        
        # Validate SFF
        sff_validation = self.validator.validate_sff(sff_data)
        
        if not sff_validation['valid']:
            return f"SFF validation failed: {sff_validation['errors']}"
        
        # Save to file
        output_path = self._save_sff_file(sff_data)
        
        # Add to knowledge base
        self.flowsheet_retriever.add_flowsheet(sff_data)
        
        # Generate response
        response = self.dialogue_manager.generate_finalization_message(
            output_path=output_path,
            validation=sff_validation
        )
        
        return response
    
    def _check_missing_critical_info(self) -> List[str]:
        """Check for missing critical information."""
        critical_fields = ['product', 'scale', 'feedstock']
        missing = []
        
        for field in critical_fields:
            if field not in self.current_requirements:
                missing.append(field)
        
        return missing
    
    def _save_sff_file(self, sff_data: dict) -> Path:
        """Save SFF file to designs directory."""
        # Create designs directory if it doesn't exist
        designs_dir = Path("designs")
        designs_dir.mkdir(exist_ok=True)
        
        # Generate filename
        product = self.current_requirements['product'].replace(' ', '_').lower()
        feedstock = self.current_requirements.get('feedstock', 'unknown').replace(' ', '_').lower()
        filename = f"{product}_from_{feedstock}.json"
        
        output_path = designs_dir / filename
        
        # Save file
        with open(output_path, 'w') as f:
            json.dump(sff_data, f, indent=2)
        
        return output_path
    
    def get_design_summary(self) -> Optional[Dict[str, Any]]:
        """Get summary of current design."""
        if not self.design_in_progress:
            return None
        
        flowsheet = self.design_in_progress['flowsheet']
        
        return {
            'num_units': len(flowsheet.units),
            'num_streams': len(flowsheet.streams),
            'unit_types': list(set([u.type for u in flowsheet.units.values()])),
            'validation_status': self.design_in_progress['validation']['status'],
            'reference_flowsheets': [r['flowsheet_id'] for r in self.design_in_progress['references']]
        }
    
    def export_design_report(self, output_path: str):
        """Export comprehensive design report."""
        if not self.design_in_progress:
            raise ValueError("No design available to export")
        
        # Implementation for comprehensive report generation
        # Would include:
        # - Design overview
        # - Unit specifications
        # - Stream table
        # - Mass and energy balances
        # - Cost estimates
        # - P&ID diagram
        pass


if __name__ == "__main__":
    # Example usage
    agent = FlowsheetDesignAgent()
    
    print(agent.start_design_session())
    
    # Simulated conversation
    responses = [
        "I want to produce bioethanol from corn stover",
        "50 tonnes per day, fuel grade purity",
        "Yes to enzyme recycling, use steam heating"
    ]
    
    for user_input in responses:
        print(f"\nUser: {user_input}")
        response = agent.process_user_input(user_input)
        print(f"\nAgent: {response}")

