CREATE TABLE IF NOT EXISTS post_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(255) NOT NULL,
    post_id VARCHAR(255) NOT NULL,
    location_id VARCHAR(255) NOT NULL,
    action VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    modified_by VARCHAR(255),
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_batch_id (batch_id),
    INDEX idx_post_id (post_id),
    INDEX idx_location_id (location_id),
    INDEX idx_action (action),
    INDEX idx_created_at (created_at)
);
