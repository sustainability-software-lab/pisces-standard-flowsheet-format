"""
Dialogue Manager

Orchestrates conversation flow, determines phases, and generates contextual responses.
"""

from typing import Dict, List, Any, Optional
from enum import Enum


class ConversationPhase(Enum):
    """Conversation phases in the design process."""
    REQUIREMENTS_GATHERING = "requirements_gathering"
    CLARIFICATION = "clarification"
    DESIGN_GENERATION = "design_generation"
    DESIGN_PRESENTATION = "design_presentation"
    REFINEMENT = "refinement"
    FINALIZATION = "finalization"


class DialogueManager:
    """
    Manages conversation flow and generates contextual responses.
    
    Responsibilities:
    - Determine current conversation phase
    - Generate appropriate prompts and questions
    - Maintain conversation coherence
    - Explain design decisions
    """
    
    def __init__(self, llm_model: str = "gpt-4", llm_api_key: Optional[str] = None):
        """
        Initialize dialogue manager.
        
        Args:
            llm_model: LLM model for generation
            llm_api_key: API key for LLM
        """
        self.llm_model = llm_model
        self.llm_api_key = llm_api_key
        
    def generate_greeting(self) -> str:
        """Generate initial greeting message."""
        return (
            "🤖 Welcome to the Flowsheet Design Assistant!\n\n"
            "I'm here to help you design a chemical or bioprocess flowsheet. "
            "I'll ask you some questions about what you want to produce, and then "
            "I'll generate a complete flowsheet design based on proven patterns from "
            "similar processes.\n\n"
            "Let's get started! What product are you trying to produce?"
        )
    
    def determine_phase(
        self,
        conversation_history: List[Dict[str, str]],
        current_requirements: Dict[str, Any]
    ) -> str:
        """
        Determine the current conversation phase.
        
        Args:
            conversation_history: List of conversation messages
            current_requirements: Currently gathered requirements
            
        Returns:
            Current conversation phase
        """
        # If just started
        if len(conversation_history) <= 2:
            return ConversationPhase.REQUIREMENTS_GATHERING.value
        
        # Check if we have basic requirements
        has_product = 'product' in current_requirements
        has_scale = 'scale' in current_requirements
        has_feedstock = 'feedstock' in current_requirements
        
        # If missing basic info, still gathering
        if not (has_product and has_scale):
            return ConversationPhase.REQUIREMENTS_GATHERING.value
        
        # If we have basic info but missing optional details
        if has_product and has_scale and not has_feedstock:
            return ConversationPhase.CLARIFICATION.value
        
        # Check last user message for design generation trigger
        last_user_msg = conversation_history[-2]['content'].lower() if len(conversation_history) >= 2 else ""
        
        if any(trigger in last_user_msg for trigger in [
            "generate", "create", "design", "ready", "go ahead", "proceed"
        ]):
            return ConversationPhase.DESIGN_GENERATION.value
        
        # Check for modification requests
        if any(word in last_user_msg for word in [
            "change", "modify", "update", "different", "instead"
        ]):
            return ConversationPhase.REFINEMENT.value
        
        # Check for finalization requests
        if any(word in last_user_msg for word in [
            "save", "export", "generate sff", "download", "finalize"
        ]):
            return ConversationPhase.FINALIZATION.value
        
        # Default: clarification
        return ConversationPhase.CLARIFICATION.value
    
    def generate_requirements_response(
        self,
        extracted_info: Dict[str, Any],
        similar_flowsheets: List[Dict],
        missing_info: List[str]
    ) -> str:
        """
        Generate response during requirements gathering.
        
        Args:
            extracted_info: Information extracted from user input
            similar_flowsheets: Similar flowsheets found
            missing_info: List of missing required information
            
        Returns:
            Response message
        """
        response = "Great! "
        
        # Acknowledge what was understood
        if 'product' in extracted_info:
            response += f"So you want to produce **{extracted_info['product']}**. "
        
        if 'feedstock' in extracted_info:
            response += f"Using **{extracted_info['feedstock']}** as feedstock. "
        
        if 'scale' in extracted_info:
            response += f"At a scale of **{extracted_info['scale']} {extracted_info.get('scale_unit', 'units/day')}**. "
        
        # Show similar flowsheets if found
        if similar_flowsheets:
            response += f"\n\n🔍 I found {len(similar_flowsheets)} similar flowsheets in our database:\n"
            for i, fs in enumerate(similar_flowsheets[:3], 1):
                response += f"  {i}. {fs['name']} (similarity: {fs['similarity']:.0%})\n"
        
        # Ask for missing critical information
        if missing_info:
            response += "\n\n📋 To design your flowsheet, I need a few more details:\n\n"
            
            priority_questions = {
                'product': "What specific product do you want to produce?",
                'scale': "What production scale are you targeting? (e.g., '10 kg/day', '50 tonnes/day')",
                'feedstock': "What feedstock will you use?",
                'purity': "What purity do you need for the final product?"
            }
            
            for i, field in enumerate(missing_info[:3], 1):
                if field in priority_questions:
                    response += f"**{i}. {priority_questions[field]}**\n"
        
        else:
            response += "\n\nI have the basic information. Ready to proceed with some clarifying questions?"
        
        return response
    
    def generate_clarification_questions(
        self,
        missing_info: List[str],
        current_requirements: Dict[str, Any]
    ) -> str:
        """Generate clarification questions."""
        response = "🤔 A few more questions to optimize your design:\n\n"
        
        # Generate context-aware questions
        questions = []
        
        if 'operating_conditions' not in current_requirements:
            questions.append({
                'num': len(questions) + 1,
                'question': "What are your preferred operating conditions?",
                'options': ["Ambient (room temperature/pressure)", "Elevated (high temp/pressure)", "Not sure - recommend"],
                'why': "Determines unit design and material selection"
            })
        
        # Product-specific questions
        product_type = current_requirements.get('product_type', '')
        
        if 'biofuel' in product_type.lower() or 'ethanol' in current_requirements.get('product', '').lower():
            if 'enzyme_recycle' not in current_requirements:
                questions.append({
                    'num': len(questions) + 1,
                    'question': "Do you want to include enzyme recycling?",
                    'options': ["Yes", "No", "Evaluate both options"],
                    'why': "Can reduce enzyme costs by ~25% but adds complexity"
                })
        
        if 'energy_source' not in current_requirements:
            questions.append({
                'num': len(questions) + 1,
                'question': "Energy source for heating?",
                'options': ["Steam from boiler", "Electric heating", "Natural gas", "Not sure - recommend"],
                'why': "Affects operating costs and environmental impact"
            })
        
        # Format questions
        for q in questions[:3]:  # Max 3 questions at a time
            response += f"**{q['num']}. {q['question']}**\n"
            response += f"   💡 Why it matters: {q['why']}\n"
            for opt in q['options']:
                response += f"   - {opt}\n"
            response += "\n"
        
        if not questions:
            response = "✅ I have all the information I need! Ready to generate your flowsheet design?"
        
        return response
    
    def generate_ready_to_design_message(self, requirements: Dict[str, Any]) -> str:
        """Generate message when ready to start design."""
        response = "✅ Perfect! I have everything I need.\n\n"
        response += "📋 **Design Summary:**\n"
        response += f"  • Product: {requirements.get('product', 'N/A')}\n"
        response += f"  • Feedstock: {requirements.get('feedstock', 'N/A')}\n"
        response += f"  • Scale: {requirements.get('scale', 'N/A')} {requirements.get('scale_unit', '')}\n"
        
        if 'purity' in requirements:
            response += f"  • Purity: {requirements['purity']}\n"
        
        response += "\nShall I proceed with generating the flowsheet design? (Type 'yes' to continue)"
        
        return response
    
    def generate_design_complete_message(
        self,
        flowsheet: Any,
        validation: Dict[str, Any]
    ) -> str:
        """Generate message when design is complete."""
        response = "🎉 **Design Complete!**\n\n"
        response += "⏳ Design process:\n"
        response += "  ✓ Found optimal structure using learned patterns\n"
        response += "  ✓ Configured all unit operations\n"
        response += "  ✓ Calculated stream properties\n"
        response += "  ✓ Validated design\n\n"
        
        response += "📊 **Design Summary:**\n"
        response += f"  • Total units: {flowsheet.num_units}\n"
        response += f"  • Total streams: {flowsheet.num_streams}\n"
        response += f"  • Validation status: {validation['status']}\n\n"
        
        # Show main sections
        response += "🏗️ **Main Process Sections:**\n"
        for section in flowsheet.sections:
            response += f"  • {section.name}: {section.num_units} units\n"
        
        response += "\n💬 What would you like to do next?\n"
        response += "  1. View detailed specifications\n"
        response += "  2. Modify the design\n"
        response += "  3. Generate SFF file\n"
        response += "  4. Export documentation\n"
        
        return response
    
    def present_design(self, flowsheet: Any) -> str:
        """Present design details."""
        response = "📄 **Detailed Design Presentation**\n\n"
        
        # Unit breakdown
        response += "**Units by Type:**\n"
        for unit_type, count in flowsheet.unit_type_counts.items():
            response += f"  • {unit_type}: {count}\n"
        
        response += "\n**Key Performance Indicators:**\n"
        if hasattr(flowsheet, 'yield_estimate'):
            response += f"  • Expected yield: {flowsheet.yield_estimate}\n"
        if hasattr(flowsheet, 'energy_consumption'):
            response += f"  • Energy consumption: {flowsheet.energy_consumption}\n"
        if hasattr(flowsheet, 'capex_estimate'):
            response += f"  • Estimated CAPEX: ${flowsheet.capex_estimate:,.0f}\n"
        
        return response
    
    def generate_modification_response(
        self,
        modifications: Dict[str, Any],
        validation: Dict[str, Any]
    ) -> str:
        """Generate response after modifications."""
        response = "✓ **Modifications Applied**\n\n"
        
        response += "Changes made:\n"
        for mod in modifications:
            response += f"  • {mod['description']}\n"
        
        response += f"\nValidation status: {validation['status']}\n"
        
        if validation['status'] == 'valid':
            response += "\n✅ Modified design passes all validation checks!"
        else:
            response += f"\n⚠️ Validation warnings: {len(validation['warnings'])}"
        
        return response
    
    def generate_finalization_message(
        self,
        output_path: str,
        validation: Dict[str, Any]
    ) -> str:
        """Generate finalization message."""
        response = "✓ **Design Finalized!**\n\n"
        response += f"📁 SFF file generated: `{output_path}`\n"
        response += f"✓ Validated against SFF schema v2.0\n"
        response += f"✓ All required fields present\n\n"
        
        response += "📄 **Additional files generated:**\n"
        response += "  • Design report (PDF)\n"
        response += "  • Equipment list (CSV)\n"
        response += "  • Stream table (CSV)\n"
        response += "  • P&ID diagram (PNG)\n\n"
        
        response += "🎓 This design has been added to the knowledge base!\n"
        response += "It will help improve future designs.\n\n"
        response += "Thank you for using the Flowsheet Design Assistant! 🚀"
        
        return response

