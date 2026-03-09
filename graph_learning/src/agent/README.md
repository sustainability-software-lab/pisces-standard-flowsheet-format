# Flowsheet Design Agent

An AI agent for automated chemical/bioprocess flowsheet design through intelligent dialogue.

## 📁 Structure

```
src/agent/
├── flowsheet_agent.py        # Main agent orchestrator
├── dialogue_manager.py        # Conversation flow management
├── tools/                     # Agent tools
│   ├── requirement_extractor.py   # Extract requirements from user input
│   ├── structure_generator.py     # Generate flowsheet structure
│   ├── sff_generator.py          # Create SFF files
│   └── design_validator.py       # Validate designs
└── knowledge_base/            # Knowledge storage
    ├── flowsheet_retriever.py     # Search similar flowsheets
    └── unit_library.py           # Unit operation library
```

## 🚀 Quick Start

```python
from src.agent import FlowsheetDesignAgent

# Initialize agent
agent = FlowsheetDesignAgent(
    knowledge_base_path="data/knowledge_base",
    model_path="models/graph_vae.pth",
    schema_path="schema/sff_schema.json"
)

# Start design session
greeting = agent.start_design_session()
print(greeting)

# Process user input
response = agent.process_user_input("I want to make bioethanol from corn stover")
print(response)

# Continue conversation...
```

## 🎯 Capabilities

### 1. Intelligent Requirements Gathering
- Extracts structured information from natural language
- Asks prioritized clarifying questions
- Identifies missing critical parameters
- Provides context for each question

### 2. Knowledge-Based Design
- Searches for similar existing flowsheets
- Retrieves relevant design patterns
- Leverages historical data for recommendations
- Explains design rationale

### 3. Structure Generation
- Uses trained GNN models for topology
- Applies domain-specific constraints
- Configures unit operations
- Calculates stream properties

### 4. Interactive Refinement
- Accepts modification requests
- Iteratively improves design
- Validates each change
- Explains impacts

### 5. SFF Generation & Validation
- Creates schema-compliant SFF files
- Validates connectivity and feasibility
- Checks mass balances
- Generates documentation

## 📖 Usage Examples

### Example 1: Complete Design Session

```python
agent = FlowsheetDesignAgent()

# Start
print(agent.start_design_session())

# Provide requirements
agent.process_user_input("I want to produce bioethanol")
agent.process_user_input("From corn stover, 50 tonnes per day")
agent.process_user_input("Yes to enzyme recycling, use steam heating")

# Generate design
agent.process_user_input("Please generate the design")

# Get summary
summary = agent.get_design_summary()
print(summary)

# Finalize
agent.process_user_input("Generate SFF file")
```

### Example 2: With Modifications

```python
# After initial design
agent.process_user_input("Can you add a water recycle system?")
agent.process_user_input("Increase the fermentation capacity by 20%")

# Finalize modified design
agent.process_user_input("Generate SFF file")
```

## 🛠️ Implementation Status

### ✅ Completed (Phase 1)
- Agent architecture defined
- Dialogue manager framework
- Tool interface specifications
- Conversation flow logic

### 🔄 In Progress (Phase 2-3)
- Requirement extractor (LLM integration)
- Similarity search implementation
- GNN structure generator integration
- Unit configuration engine
- Stream calculator

### 📋 Planned (Phase 4+)
- Advanced validation rules
- Cost estimation
- Optimization capabilities
- Multi-agent collaboration
- Continuous learning

## 🔧 Configuration

### Knowledge Base Setup

```python
# Index existing flowsheets
from src.agent.knowledge_base import build_knowledge_base

build_knowledge_base(
    flowsheet_dir="exported_flowsheets",
    output_dir="data/knowledge_base"
)
```

### LLM Configuration

```python
# Using OpenAI
agent = FlowsheetDesignAgent(
    llm_model="gpt-4",
    llm_api_key="your-api-key"
)

# Using local model
agent = FlowsheetDesignAgent(
    llm_model="llama-2-70b",
    llm_api_key=None
)
```

## 📊 Conversation Phases

The agent manages conversations through distinct phases:

1. **Requirements Gathering**: Initial product/feedstock/scale
2. **Clarification**: Detailed questions for optimization
3. **Design Generation**: Structure and configuration
4. **Design Presentation**: Show results and metrics
5. **Refinement**: Iterative modifications
6. **Finalization**: SFF generation and export

## 🎨 Example Conversations

See `examples/agent_conversations.md` for detailed conversation examples.

## 🔌 API Integration

The agent can be used programmatically or through various interfaces:

### CLI Interface
```bash
python -m src.agent.cli
```

### Web Interface (Streamlit)
```bash
streamlit run src/agent/web_app.py
```

### API Server (FastAPI)
```bash
uvicorn src.agent.api:app --reload
```

## 📈 Metrics & Evaluation

Track agent performance:
- Design quality (schema compliance, validity)
- User experience (questions asked, iterations needed)
- Knowledge leverage (% using similar flowsheets)
- Generation speed (time to first design)

## 🤝 Contributing

To add new capabilities:

1. Implement tool in `tools/`
2. Add to agent workflow in `flowsheet_agent.py`
3. Update dialogue manager for new questions
4. Add tests in `tests/agent/`

## 📚 Documentation

- [Strategy Document](../../FLOWSHEET_DESIGN_AGENT_STRATEGY.md) - Complete strategic plan
- [Tool Specifications](docs/tool_specs.md) - Detailed tool documentation
- [Knowledge Base Schema](docs/knowledge_base.md) - Data structure specs
- [Conversation Design](docs/conversation_design.md) - Dialogue patterns

## 🐛 Troubleshooting

### Common Issues

**Issue**: Agent asks too many questions
- **Solution**: Adjust question prioritization in dialogue_manager.py

**Issue**: Generated designs invalid
- **Solution**: Check SFF schema path and validator rules

**Issue**: No similar flowsheets found
- **Solution**: Rebuild knowledge base index

## 🔮 Future Enhancements

- Multi-objective optimization
- Integration with process simulators (Aspen Plus, DWSIM)
- Collaborative multi-agent design
- Reinforcement learning from user feedback
- Economic analysis and sensitivity studies
- Environmental impact assessment

---

**Ready to design flowsheets with AI!** 🚀

For questions or issues, see the main project README or open an issue on GitHub.

