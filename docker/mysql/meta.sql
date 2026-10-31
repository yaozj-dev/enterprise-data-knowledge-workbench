SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS meta DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON meta.* TO 'workbench_meta'@'%';

USE meta;

DROP TABLE IF EXISTS table_info;
CREATE TABLE table_info
(
    id          VARCHAR(64) PRIMARY KEY COMMENT '表编号',
    name        VARCHAR(128) COMMENT '表名称',
    role        VARCHAR(32) COMMENT '表类型(fact/dim)',
    description TEXT COMMENT '表描述'
);



DROP TABLE IF EXISTS column_info;
CREATE TABLE column_info
(
    id          VARCHAR(64) PRIMARY KEY COMMENT '列编号',
    name        VARCHAR(128) COMMENT '列名称',
    type        VARCHAR(64) COMMENT '数据类型',
    role        VARCHAR(32) COMMENT '列类型(primary_key,foreign_key,measure,dimension)',
    examples    JSON COMMENT '数据示例',
    description TEXT COMMENT '列描述',
    alias       JSON COMMENT '列别名',
    table_id    VARCHAR(64) COMMENT '所属表编号'
);

DROP TABLE IF EXISTS metric_info;
CREATE TABLE metric_info
(
    id               VARCHAR(64) PRIMARY KEY COMMENT '指标编码',
    name             VARCHAR(128) COMMENT '指标名称',
    description      TEXT COMMENT '指标描述',
    relevant_columns JSON COMMENT '关联的列',
    alias            JSON COMMENT '指标别名'
);


DROP TABLE IF EXISTS column_metric;
CREATE TABLE column_metric
(
    column_id VARCHAR(64) COMMENT '列编号',
    metric_id VARCHAR(64) COMMENT '指标编号',
    PRIMARY KEY (column_id, metric_id)
);

CREATE TABLE IF NOT EXISTS agent_memory_items
(
    id              VARCHAR(64) PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    conversation_id VARCHAR(128),
    kind            VARCHAR(40) NOT NULL,
    content         TEXT NOT NULL,
    source_run_id   VARCHAR(128),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at    TIMESTAMP NULL,
    INDEX idx_memory_user_status (user_id, status)
);

CREATE TABLE IF NOT EXISTS conversation_summaries
(
    id              VARCHAR(64) PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    conversation_id VARCHAR(128) NOT NULL,
    summary         TEXT NOT NULL,
    source_run_id   VARCHAR(128),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_summary_conversation (user_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS memory_audit_logs
(
    id         BIGINT PRIMARY KEY AUTO_INCREMENT,
    memory_id  VARCHAR(64) NOT NULL,
    user_id    BIGINT NOT NULL,
    action     VARCHAR(30) NOT NULL,
    detail     VARCHAR(1000),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_memory_audit_user (user_id, created_at)
);

CREATE TABLE IF NOT EXISTS mcp_tool_audit_logs
(
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT NOT NULL,
    conversation_id VARCHAR(128),
    run_id          VARCHAR(128),
    agent_name      VARCHAR(40),
    tool_name       VARCHAR(80) NOT NULL,
    source          VARCHAR(20) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    error_code      VARCHAR(80),
    retryable       BOOLEAN NOT NULL DEFAULT FALSE,
    duration_ms     INT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_mcp_audit_user_time (user_id, created_at),
    INDEX idx_mcp_audit_tool_time (tool_name, created_at)
);
