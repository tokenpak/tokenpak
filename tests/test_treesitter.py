"""Unit tests for code_treesitter.py (Part D — Tree-sitter Code Processing)."""

import warnings
import pytest

from tokenpak.processors.code_treesitter import (
    extract,
    is_available,
    TreeSitterProcessor,
    _detect_language,
    EXTENSION_TO_LANG,
)
from tokenpak.processors import get_processor


# ---------------------------------------------------------------------------
# Skip all tests if tree-sitter is not available
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="tree-sitter-languages not available",
)


# ---------------------------------------------------------------------------
# Helper: suppress tree_sitter FutureWarning in assertions
# ---------------------------------------------------------------------------

def _extract(source, path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return extract(source, path)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_py(self):
        assert _detect_language("src/auth.py") == "python"

    def test_js(self):
        assert _detect_language("app.js") == "javascript"

    def test_jsx(self):
        assert _detect_language("Component.jsx") == "javascript"

    def test_ts(self):
        assert _detect_language("service.ts") == "typescript"

    def test_tsx(self):
        assert _detect_language("App.tsx") == "typescript"

    def test_go(self):
        assert _detect_language("main.go") == "go"

    def test_rs(self):
        assert _detect_language("lib.rs") == "rust"

    def test_unknown_returns_none(self):
        assert _detect_language("README.md") is None

    def test_case_insensitive(self):
        assert _detect_language("MAIN.GO") == "go"


# ---------------------------------------------------------------------------
# Python extraction
# ---------------------------------------------------------------------------

_PY_SAMPLE = '''\
import os
from pathlib import Path

MY_CONST = 42


class BlockRegistry:
    """Manages versioned content blocks."""

    db_path: str = ""

    def __init__(self, db_path: str):
        """Initialize the registry."""
        self.path = db_path
        self.items = []

    def add_block(self, block) -> str:
        """Add a block to the registry."""
        self.items.append(block)
        return str(block)

    def search(self, query: str, top_k: int = 10) -> list:
        return self.items[:top_k]


def standalone_fn(a: int, b: int) -> int:
    """Compute sum."""
    return a + b


def _private_fn():
    x = 1
    y = 2
    return x + y
'''

class TestPythonExtraction:
    def setup_method(self):
        self.result = _extract(_PY_SAMPLE, "registry.py")

    def test_imports_preserved(self):
        assert "import os" in self.result
        assert "from pathlib import Path" in self.result

    def test_class_signature_preserved(self):
        assert "class BlockRegistry" in self.result

    def test_class_docstring_preserved(self):
        assert "Manages versioned content blocks" in self.result

    def test_method_signatures_preserved(self):
        assert "def __init__" in self.result
        assert "def add_block" in self.result
        assert "def search" in self.result

    def test_method_bodies_replaced(self):
        assert "self.items.append(block)" not in self.result
        assert "return str(block)" not in self.result

    def test_body_stub_present(self):
        assert "..." in self.result

    def test_constants_preserved(self):
        assert "MY_CONST = 42" in self.result

    def test_standalone_function_preserved(self):
        assert "def standalone_fn" in self.result

    def test_private_function_preserved(self):
        assert "def _private_fn" in self.result

    def test_extraction_removes_more_than_it_keeps(self):
        # Extracted output should be shorter than original — bodies were dropped
        assert len(self.result) < len(_PY_SAMPLE)


# ---------------------------------------------------------------------------
# JavaScript / TypeScript extraction
# ---------------------------------------------------------------------------

_JS_SAMPLE = '''\
import { EventEmitter } from 'events';
import path from 'path';

const MAX_CONNECTIONS = 10;

class ConnectionPool {
  constructor(size) {
    this.size = size;
    this.pool = [];
  }

  acquire() {
    if (this.pool.length === 0) {
      throw new Error('Pool exhausted');
    }
    return this.pool.pop();
  }

  release(conn) {
    this.pool.push(conn);
  }
}

function createPool(size) {
  const pool = [];
  for (let i = 0; i < size; i++) {
    pool.push(new Connection());
  }
  return pool;
}

export function exportedFn(x, y) {
  return x * y;
}
'''

class TestJavaScriptExtraction:
    def setup_method(self):
        self.result = _extract(_JS_SAMPLE, "pool.js")

    def test_imports_preserved(self):
        assert "import { EventEmitter } from 'events'" in self.result
        assert "import path from 'path'" in self.result

    def test_class_preserved(self):
        assert "class ConnectionPool" in self.result

    def test_method_stubs_present(self):
        assert "acquire" in self.result
        assert "release" in self.result

    def test_function_signature_preserved(self):
        assert "function createPool" in self.result

    def test_function_body_removed(self):
        assert "Pool exhausted" not in self.result
        assert "pool.push(new Connection())" not in self.result

    def test_constant_preserved(self):
        assert "MAX_CONNECTIONS" in self.result

    def test_export_preserved(self):
        assert "exportedFn" in self.result


_TS_SAMPLE = '''\
import { Injectable } from '@angular/core';

interface UserService {
  findById(id: number): Promise<User>;
  create(data: CreateUserDto): Promise<User>;
}

class UserServiceImpl implements UserService {
  constructor(private readonly db: Database) {}

  async findById(id: number): Promise<User> {
    const user = await this.db.query(`SELECT * FROM users WHERE id = ${id}`);
    return user;
  }

  async create(data: CreateUserDto): Promise<User> {
    return this.db.insert('users', data);
  }
}

export function getService(): UserService {
  return new UserServiceImpl(getDb());
}
'''

class TestTypeScriptExtraction:
    def setup_method(self):
        self.result = _extract(_TS_SAMPLE, "user.service.ts")

    def test_imports_preserved(self):
        assert "@angular/core" in self.result

    def test_class_preserved(self):
        assert "UserServiceImpl" in self.result

    def test_methods_preserved(self):
        assert "findById" in self.result
        assert "create" in self.result

    def test_bodies_not_in_output(self):
        assert "SELECT * FROM users" not in self.result


# ---------------------------------------------------------------------------
# Go extraction
# ---------------------------------------------------------------------------

_GO_SAMPLE = '''\
package main

import (
	"fmt"
	"os"
)

type UserRepository struct {
	db *Database
}

type Config struct {
	Host string
	Port int
}

func NewUserRepo(db *Database) *UserRepository {
	return &UserRepository{db: db}
}

func (r *UserRepository) FindByID(id int) (*User, error) {
	row := r.db.QueryRow("SELECT * FROM users WHERE id = ?", id)
	return scanUser(row)
}

func (r *UserRepository) Delete(id int) error {
	_, err := r.db.Exec("DELETE FROM users WHERE id = ?", id)
	return err
}

const MaxRetries = 3
var DefaultTimeout = 30
'''

class TestGoExtraction:
    def setup_method(self):
        self.result = _extract(_GO_SAMPLE, "repo.go")

    def test_package_preserved(self):
        assert "package main" in self.result

    def test_imports_preserved(self):
        assert "fmt" in self.result
        assert "os" in self.result

    def test_struct_preserved(self):
        assert "UserRepository" in self.result
        assert "Config" in self.result

    def test_function_signatures_preserved(self):
        assert "NewUserRepo" in self.result
        assert "FindByID" in self.result
        assert "Delete" in self.result

    def test_function_bodies_removed(self):
        assert "QueryRow" not in self.result
        assert "db.Exec" not in self.result

    def test_stub_brackets_present(self):
        assert "{}" in self.result

    def test_constants_preserved(self):
        assert "MaxRetries" in self.result


# ---------------------------------------------------------------------------
# Rust extraction
# ---------------------------------------------------------------------------

_RS_SAMPLE = '''\
use std::collections::HashMap;
use crate::db::Database;

pub struct UserRepository {
    db: Database,
    cache: HashMap<u64, User>,
}

pub enum UserStatus {
    Active,
    Inactive,
    Banned,
}

impl UserRepository {
    pub fn new(db: Database) -> Self {
        UserRepository { db, cache: HashMap::new() }
    }

    pub fn find_by_id(&self, id: u64) -> Option<&User> {
        self.cache.get(&id)
    }

    pub fn insert(&mut self, user: User) -> Result<(), DbError> {
        let id = user.id;
        self.cache.insert(id, user);
        self.db.save(&self.cache[&id])
    }
}

pub fn create_repo(db: Database) -> UserRepository {
    UserRepository::new(db)
}

const MAX_CACHE_SIZE: usize = 1024;
'''

class TestRustExtraction:
    def setup_method(self):
        self.result = _extract(_RS_SAMPLE, "repo.rs")

    def test_use_declarations_preserved(self):
        assert "use std::collections::HashMap" in self.result
        assert "use crate::db::Database" in self.result

    def test_struct_preserved(self):
        assert "pub struct UserRepository" in self.result

    def test_enum_preserved(self):
        assert "pub enum UserStatus" in self.result

    def test_impl_methods_preserved(self):
        assert "pub fn new" in self.result
        assert "pub fn find_by_id" in self.result
        assert "pub fn insert" in self.result

    def test_function_bodies_removed(self):
        assert "HashMap::new()" not in self.result
        assert "self.cache.get" not in self.result

    def test_standalone_fn_preserved(self):
        assert "create_repo" in self.result

    def test_const_preserved(self):
        assert "MAX_CACHE_SIZE" in self.result


# ---------------------------------------------------------------------------
# Fallback on unsupported / parse failure
# ---------------------------------------------------------------------------

class TestFallback:
    def test_unsupported_extension_returns_none(self):
        result = _extract("some content", "file.rb")
        assert result is None

    def test_treesitter_processor_falls_back_on_unsupported(self):
        proc = TreeSitterProcessor()
        result = proc.process("some ruby code", "file.rb")
        # Should use CodeProcessor fallback — just check it returns something
        assert isinstance(result, str)
        assert len(result) > 0

    def test_treesitter_processor_handles_empty_input(self):
        proc = TreeSitterProcessor()
        result = proc.process("", "empty.py")
        assert isinstance(result, str)

    def test_get_processor_no_treesitter_flag(self):
        proc = get_processor("code", no_treesitter=True)
        from tokenpak.processors.code import CodeProcessor
        assert isinstance(proc, CodeProcessor)

    def test_get_processor_default_uses_treesitter(self):
        proc = get_processor("code")
        # Should be TreeSitterProcessor when tree-sitter is available
        assert isinstance(proc, TreeSitterProcessor)


# ---------------------------------------------------------------------------
# Compression ratio (target ≥3x on body-heavy code files)
# The spec says "3-10x on typical code files" — verified using character count
# on files where bodies dominate over signatures.
# ---------------------------------------------------------------------------

# Python: 10 methods each with a 15-20 line body
_PY_HEAVY = '''\
import os
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
MAX_RETRIES = 5
DEFAULT_TIMEOUT = 30


class DataPipeline:
    """Orchestrates multi-stage data processing with retry and checkpointing."""

    def __init__(self, config: Dict, output_dir: str):
        """Initialize pipeline with config and output directory."""
        self.config = config
        self.output_dir = Path(output_dir)
        self.stages = []
        self.checkpoint_data = {}
        self.metrics = {"processed": 0, "failed": 0, "skipped": 0}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()

    def _setup_logging(self):
        handler = logging.FileHandler(self.output_dir / "pipeline.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    def add_stage(self, name: str, fn, retries: int = 3) -> None:
        """Register a processing stage."""
        if name in [s["name"] for s in self.stages]:
            raise ValueError(f"Stage {name!r} already registered")
        self.stages.append({"name": name, "fn": fn, "retries": retries})
        logger.info(f"Stage added: {name}")

    def run(self, data: List[Dict]) -> List[Dict]:
        """Run all stages on input data, returns processed results."""
        results = data
        for stage in self.stages:
            logger.info(f"Running stage: {stage['name']}")
            attempt = 0
            while attempt < stage["retries"]:
                try:
                    results = [stage["fn"](item) for item in results
                               if item is not None]
                    self.metrics["processed"] += len(results)
                    self._checkpoint(stage["name"], results)
                    break
                except Exception as exc:
                    attempt += 1
                    logger.warning(f"Stage {stage['name']} attempt {attempt} failed: {exc}")
                    if attempt >= stage["retries"]:
                        self.metrics["failed"] += len(results)
                        results = []
        return results

    def _checkpoint(self, stage: str, data: List[Dict]) -> None:
        """Save a checkpoint after each stage completes."""
        ts = datetime.now(timezone.utc).isoformat()
        self.checkpoint_data[stage] = {"timestamp": ts, "count": len(data)}
        path = self.output_dir / f"checkpoint_{stage}.json"
        with open(path, "w") as f:
            json.dump({"stage": stage, "ts": ts, "data": data}, f, indent=2)

    def load_checkpoint(self, stage: str) -> Optional[List[Dict]]:
        """Load a previously saved checkpoint."""
        path = self.output_dir / f"checkpoint_{stage}.json"
        if not path.exists():
            return None
        try:
            with open(path) as f:
                payload = json.load(f)
            return payload.get("data", [])
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.error(f"Failed to load checkpoint {stage}: {exc}")
            return None

    def get_metrics(self) -> Dict:
        """Return current pipeline metrics."""
        total = self.metrics["processed"] + self.metrics["failed"]
        success_rate = self.metrics["processed"] / max(total, 1)
        return {
            **self.metrics,
            "total": total,
            "success_rate": round(success_rate, 4),
            "stages": len(self.stages),
        }

    def reset(self) -> None:
        """Reset metrics and checkpoints without removing stages."""
        self.metrics = {"processed": 0, "failed": 0, "skipped": 0}
        self.checkpoint_data = {}
        for path in self.output_dir.glob("checkpoint_*.json"):
            try:
                path.unlink()
            except OSError:
                pass
        logger.info("Pipeline reset")


def build_pipeline(config_path: str) -> DataPipeline:
    """Build a DataPipeline from a JSON config file."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_file) as f:
        config = json.load(f)
    output_dir = config.get("output_dir", "/tmp/pipeline")
    pipeline = DataPipeline(config, output_dir)
    for stage_cfg in config.get("stages", []):
        name = stage_cfg["name"]
        fn = _load_stage_fn(stage_cfg["module"], stage_cfg["function"])
        retries = stage_cfg.get("retries", 3)
        pipeline.add_stage(name, fn, retries)
    return pipeline


def _load_stage_fn(module_name: str, fn_name: str):
    """Dynamically load a stage function by module + function name."""
    import importlib
    try:
        mod = importlib.import_module(module_name)
        fn = getattr(mod, fn_name, None)
        if fn is None:
            raise AttributeError(f"{module_name}.{fn_name} not found")
        return fn
    except ImportError as exc:
        raise ImportError(f"Cannot import stage module {module_name}: {exc}") from exc
'''

# Go: multiple functions each with 15+ line bodies
_GO_HEAVY = '''\
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync"
	"time"
)

const (
	DefaultTimeout = 30 * time.Second
	MaxRetries     = 5
	BufferSize     = 4096
)

type HTTPClient struct {
	baseURL    string
	timeout    time.Duration
	retries    int
	httpClient *http.Client
	mu         sync.Mutex
	headers    map[string]string
}

type Response struct {
	StatusCode int
	Body       []byte
	Headers    http.Header
}

func NewHTTPClient(baseURL string, timeout time.Duration) *HTTPClient {
	return &HTTPClient{
		baseURL: baseURL,
		timeout: timeout,
		retries: MaxRetries,
		httpClient: &http.Client{
			Timeout: timeout,
			Transport: &http.Transport{
				MaxIdleConns:    100,
				IdleConnTimeout: 90 * time.Second,
			},
		},
		headers: make(map[string]string),
	}
}

func (c *HTTPClient) SetHeader(key, value string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.headers[key] = value
}

func (c *HTTPClient) Get(ctx context.Context, path string) (*Response, error) {
	url := fmt.Sprintf("%s%s", c.baseURL, path)
	var lastErr error
	for attempt := 0; attempt < c.retries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, fmt.Errorf("creating request: %w", err)
		}
		c.mu.Lock()
		for k, v := range c.headers {
			req.Header.Set(k, v)
		}
		c.mu.Unlock()
		resp, err := c.httpClient.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(attempt+1) * 100 * time.Millisecond)
			continue
		}
		defer resp.Body.Close()
		body, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
		if err != nil {
			lastErr = err
			continue
		}
		return &Response{
			StatusCode: resp.StatusCode,
			Body:       body,
			Headers:    resp.Header,
		}, nil
	}
	return nil, fmt.Errorf("after %d attempts: %w", c.retries, lastErr)
}

func (c *HTTPClient) PostJSON(ctx context.Context, path string, payload interface{}) (*Response, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshaling payload: %w", err)
	}
	url := fmt.Sprintf("%s%s", c.baseURL, path)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	c.mu.Lock()
	for k, v := range c.headers {
		req.Header.Set(k, v)
	}
	c.mu.Unlock()
	_ = data
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return &Response{StatusCode: resp.StatusCode, Body: body, Headers: resp.Header}, nil
}

func LoadConfig(path string) (map[string]interface{}, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("opening config %s: %w", path, err)
	}
	defer f.Close()
	var cfg map[string]interface{}
	if err := json.NewDecoder(f).Decode(&cfg); err != nil {
		return nil, fmt.Errorf("decoding config: %w", err)
	}
	return cfg, nil
}
'''

# Rust: impl block with multiple methods with substantial bodies
_RS_HEAVY = '''\
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use serde::{Deserialize, Serialize};
use tokio::time::sleep;

const MAX_POOL_SIZE: usize = 100;
const IDLE_TIMEOUT_SECS: u64 = 300;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConnectionConfig {
    pub host: String,
    pub port: u16,
    pub database: String,
    pub max_connections: usize,
    pub connect_timeout: Duration,
}

#[derive(Debug)]
pub struct ConnectionPool {
    config: ConnectionConfig,
    connections: Arc<Mutex<Vec<Connection>>>,
    metrics: Arc<Mutex<PoolMetrics>>,
}

#[derive(Debug, Default)]
struct PoolMetrics {
    acquired: u64,
    released: u64,
    created: u64,
    errors: u64,
}

impl ConnectionPool {
    pub fn new(config: ConnectionConfig) -> Self {
        let pool_size = config.max_connections.min(MAX_POOL_SIZE);
        let mut conns = Vec::with_capacity(pool_size);
        for _ in 0..pool_size {
            if let Ok(conn) = Connection::new(&config.host, config.port, &config.database) {
                conns.push(conn);
            }
        }
        ConnectionPool {
            config,
            connections: Arc::new(Mutex::new(conns)),
            metrics: Arc::new(Mutex::new(PoolMetrics::default())),
        }
    }

    pub fn acquire(&self) -> Option<Connection> {
        let mut pool = self.connections.lock().unwrap();
        let mut metrics = self.metrics.lock().unwrap();
        if let Some(conn) = pool.pop() {
            metrics.acquired += 1;
            Some(conn)
        } else {
            metrics.errors += 1;
            None
        }
    }

    pub fn release(&self, conn: Connection) {
        let mut pool = self.connections.lock().unwrap();
        let mut metrics = self.metrics.lock().unwrap();
        if pool.len() < self.config.max_connections {
            pool.push(conn);
            metrics.released += 1;
        }
    }

    pub fn metrics(&self) -> HashMap<String, u64> {
        let m = self.metrics.lock().unwrap();
        let mut map = HashMap::new();
        map.insert("acquired".to_string(), m.acquired);
        map.insert("released".to_string(), m.released);
        map.insert("created".to_string(), m.created);
        map.insert("errors".to_string(), m.errors);
        map
    }

    pub async fn health_check(&self) -> bool {
        let conn = self.acquire();
        match conn {
            Some(mut c) => {
                let ok = c.ping().await.is_ok();
                self.release(c);
                ok
            }
            None => false,
        }
    }

    pub fn resize(&mut self, new_size: usize) -> usize {
        let new_size = new_size.min(MAX_POOL_SIZE).max(1);
        let mut pool = self.connections.lock().unwrap();
        if new_size > pool.len() {
            for _ in pool.len()..new_size {
                if let Ok(conn) = Connection::new(&self.config.host, self.config.port, &self.config.database) {
                    pool.push(conn);
                }
            }
        } else {
            pool.truncate(new_size);
        }
        self.config.max_connections = new_size;
        pool.len()
    }
}

pub fn create_pool(host: &str, port: u16, db: &str, size: usize) -> Result<ConnectionPool, PoolError> {
    if host.is_empty() {
        return Err(PoolError::InvalidConfig("host cannot be empty".to_string()));
    }
    if port == 0 {
        return Err(PoolError::InvalidConfig("port cannot be zero".to_string()));
    }
    let config = ConnectionConfig {
        host: host.to_string(),
        port,
        database: db.to_string(),
        max_connections: size.min(MAX_POOL_SIZE),
        connect_timeout: Duration::from_secs(30),
    };
    Ok(ConnectionPool::new(config))
}
'''


class TestCompressionRatio:
    """Verify ≥3x compression (by character count) on body-heavy code files."""

    def _char_ratio(self, source: str, path: str) -> float:
        result = _extract(source, path)
        if not result:
            return 1.0
        return len(source) / max(len(result), 1)

    def test_python_compression_3x(self):
        ratio = self._char_ratio(_PY_HEAVY, "pipeline.py")
        assert ratio >= 3.0, f"Python compression {ratio:.2f}x < 3x"

    def test_go_compression_3x(self):
        ratio = self._char_ratio(_GO_HEAVY, "client.go")
        assert ratio >= 3.0, f"Go compression {ratio:.2f}x < 3x"

    def test_rust_compression_3x(self):
        ratio = self._char_ratio(_RS_HEAVY, "pool.rs")
        assert ratio >= 3.0, f"Rust compression {ratio:.2f}x < 3x"
