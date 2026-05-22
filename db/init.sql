CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE Tenant (
    tenant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_name VARCHAR(255),
    widget_domain VARCHAR(255),
    status VARCHAR(100),
    plan_tier VARCHAR(100),
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE TABLE Chatbot_config (
    config_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    persona_prompt VARCHAR(2000),
    system_prompt VARCHAR(2000),
    escalation_enabled BOOLEAN,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE TABLE compliance_rule (
    compliance_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    restricted_topics VARCHAR(255)[],
    required_disclaimers VARCHAR(255)[]
);

CREATE TABLE knowledge_base_document (
    document_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    source_name VARCHAR(255)
);

CREATE TABLE knowledge_base_chunk (
    document_chunk_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES knowledge_base_document(document_id),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    chunk_index INTEGER,
    chunk_text VARCHAR(5000)
);

CREATE TABLE embedding (
    embedding_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_chunk_id UUID REFERENCES knowledge_base_chunk(document_chunk_id),
    document_id UUID REFERENCES knowledge_base_document(document_id),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    embedding vector(1536)
);

CREATE TABLE human_agent (
    agent_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    name VARCHAR(255),
    is_available BOOLEAN
);

CREATE TABLE chat_session (
    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    session_state VARCHAR(100),
    created_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

CREATE TABLE escalation_event (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    session_id UUID REFERENCES chat_session(session_id),
    agent_id UUID REFERENCES human_agent(agent_id),
    triggered_by VARCHAR(255),
    sentiment_trigger_score DECIMAL,
    queue_entered_at TIMESTAMPTZ,
    priority INTEGER,
    assigned_at TIMESTAMPTZ,
    handoff_state VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ
);

CREATE TABLE chat_message (
    message_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID REFERENCES chat_session(session_id),
    tenant_id UUID REFERENCES Tenant(tenant_id),
    sender_type VARCHAR(50),
    sender_id UUID,
    content TEXT,
    escalation_flag BOOLEAN,
    created_at TIMESTAMPTZ
);
