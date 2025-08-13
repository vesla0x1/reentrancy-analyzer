import React, { useState, useEffect, useRef } from 'react';
import * as d3 from 'd3';
import './App.css';
import CallGraph, { GraphNode, GraphData } from './CallGraph';

const API_URL = 'http://localhost:8000';

interface Pattern {
  type: string;
  function: string;
  severity: string;
  classification: string;
  details: string;
  external_call_target: string;
  state_changes_count: number;
}

interface Contract {
  name: string;
  type: string;
  functions_count: number;
  state_variables_count: number;
  is_abstract: boolean;
  base_contracts: string[];
  file_path: string;
}

interface FunctionInfo {
  name: string;
  contract: string;
  visibility: string;
  state_mutability: string;
  external_calls: number;
  state_changes: number;
  is_override: boolean;
}

interface Summary {
  total_contracts: number;
  total_functions: number;
  external_calls: number;
  cross_contract_calls: number;
  reentrancy_patterns: number;
  critical_issues: number;
  high_issues: number;
  medium_issues: number;
  low_issues: number;
}

interface AnalysisResults {
  status: string;
  contracts: Contract[];
  functions: FunctionInfo[];
  call_graph: GraphData;
  cfg: any;
  reentrancy_patterns: Pattern[];
  summary: Summary;
  timestamp: string;
  message?: string;
}

// Main App Component
const App: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('idle');
  const [statusMessage, setStatusMessage] = useState<string>('');
  const [results, setResults] = useState<AnalysisResults | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [highlightExternalCalls, setHighlightExternalCalls] = useState<boolean>(false);
  const [selectedContract, setSelectedContract] = useState<string | null>(null);
  const [selectedFunction, setSelectedFunction] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<string>('overview');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile && selectedFile.name.endsWith('.zip')) {
      setFile(selectedFile);
      setStatus('idle');
      setResults(null);
    } else {
      alert('Please select a ZIP file');
    }
  };

  const handleUpload = async () => {
    if (!file) {
      alert('Please select a file first');
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
      setStatus('uploading');
      setStatusMessage('Uploading project...');

      const response = await fetch(`${API_URL}/analyze`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('Upload failed');
      }

      const data = await response.json();
      setAnalysisId(data.analysis_id);
      setStatus('processing');
      setStatusMessage('Analyzing contracts...');

      // Poll for results
      pollForResults(data.analysis_id);
    } catch (error: any) {
      console.error('Error:', error);
      setStatus('error');
      setStatusMessage('Analysis failed: ' + error.message);
    }
  };

  const pollForResults = async (id: string) => {
    const maxAttempts = 60;
    let attempts = 0;

    const poll = async () => {
      try {
        const response = await fetch(`${API_URL}/analysis/${id}`);
        const data: AnalysisResults = await response.json();

        if (data.status === 'completed') {
          setStatus('completed');
          setStatusMessage('Analysis completed');
          setResults(data);
        } else if (data.status === 'error') {
          setStatus('error');
          setStatusMessage('Analysis failed: ' + (data.message || 'Unknown error'));
        } else {
          setStatusMessage(data.message || 'Processing...');
          attempts++;
          if (attempts < maxAttempts) {
            setTimeout(poll, 5000);
          } else {
            setStatus('error');
            setStatusMessage('Analysis timeout');
          }
        }
      } catch (error) {
        console.error('Polling error:', error);
        setStatus('error');
        setStatusMessage('Failed to get results');
      }
    };

    poll();
  };

  const handleNodeClick = (node: GraphNode) => {
    if (selectedNode === node.id) {
      setSelectedNode(null);
      setHighlightExternalCalls(false);
    } else {
      setSelectedNode(node.id);
      setHighlightExternalCalls(true);
    }
  };

  const renderSummary = () => {
    if (!results || !results.summary) return null;

    const summary = results.summary;
    return (
      <div className="summary-grid">
        <div className="summary-card">
          <h3>Contracts</h3>
          <div className="metric">{summary.total_contracts}</div>
        </div>
        <div className="summary-card">
          <h3>Functions</h3>
          <div className="metric">{summary.total_functions}</div>
        </div>
        <div className="summary-card">
          <h3>External Calls</h3>
          <div className="metric">{summary.external_calls}</div>
        </div>
        <div className="summary-card critical">
          <h3>Critical Issues</h3>
          <div className="metric">{summary.critical_issues}</div>
        </div>
        <div className="summary-card high">
          <h3>High Risk</h3>
          <div className="metric">{summary.high_issues}</div>
        </div>
        <div className="summary-card medium">
          <h3>Medium Risk</h3>
          <div className="metric">{summary.medium_issues}</div>
        </div>
      </div>
    );
  };

  const renderReentrancyPatterns = () => {
    if (!results || !results.reentrancy_patterns) return null;

    return (
      <div className="patterns-list">
        <h3>Reentrancy Vulnerabilities</h3>
        {results.reentrancy_patterns.length === 0 ? (
          <p className="no-issues">No reentrancy patterns detected</p>
        ) : (
          <div className="patterns-container">
            {results.reentrancy_patterns.map((pattern, index) => (
              <div key={index} className={`pattern-card ${pattern.severity}`}>
                <div className="pattern-header">
                  <span className={`severity-badge ${pattern.severity}`}>
                    {pattern.severity.toUpperCase()}
                  </span>
                  <span className="pattern-type">{pattern.classification}</span>
                </div>
                <div className="pattern-function">{pattern.function}</div>
                <div className="pattern-details">{pattern.details}</div>
                <div className="pattern-target">
                  Target: {pattern.external_call_target}
                </div>
                <div className="pattern-states">
                  State changes after: {pattern.state_changes_count}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  const renderContractList = () => {
    if (!results || !results.contracts) return null;

    return (
      <div className="contract-list">
        <h3>Contracts</h3>
        <div className="list-container">
          {results.contracts.map((contract, index) => (
            <div 
              key={index} 
              className={`contract-item ${selectedContract === contract.name ? 'selected' : ''}`}
              onClick={() => {
                setSelectedContract(contract.name);
                setViewMode('contracts');
              }}
            >
              <div className="contract-name">{contract.name}</div>
              <div className="contract-info">
                <span className="badge">{contract.type}</span>
                <span>Functions: {contract.functions_count}</span>
                <span>Variables: {contract.state_variables_count}</span>
              </div>
              {contract.base_contracts.length > 0 && (
                <div className="contract-inheritance">
                  Inherits: {contract.base_contracts.join(', ')}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderFunctionList = () => {
    if (!results || !results.functions) return null;

    const functions = selectedContract 
      ? results.functions.filter(f => f.contract === selectedContract)
      : results.functions;

    return (
      <div className="function-list">
        <h3>Functions {selectedContract && `in ${selectedContract}`}</h3>
        <div className="list-container">
          {functions.map((func, index) => (
            <div 
              key={index} 
              className={`function-item ${selectedFunction === func.name ? 'selected' : ''}`}
              onClick={() => {
                setSelectedFunction(func.name);
                setSelectedNode(func.name);
                setViewMode('function');
              }}
            >
              <div className="function-name">{func.name}</div>
              <div className="function-info">
                <span className={`visibility-badge ${func.visibility}`}>
                  {func.visibility}
                </span>
                {func.state_mutability && (
                  <span className="mutability-badge">{func.state_mutability}</span>
                )}
                <span>External: {func.external_calls}</span>
                <span>State Changes: {func.state_changes}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>Reentrancy Analyzer</h1>
        <p>Upload your Foundry project to detect reentrancy vulnerabilities</p>
      </header>

      <div className="main-container">
        {status === 'idle' || status === 'error' ? (
          <div className="upload-section">
            <div className="upload-card">
              <h2>Upload Project</h2>
              <input 
                type="file" 
                accept=".zip" 
                onChange={handleFileChange}
                className="file-input"
                id="file-upload"
              />
              <label htmlFor="file-upload" className="file-label">
                {file ? file.name : 'Choose ZIP file...'}
              </label>
              <button 
                onClick={handleUpload} 
                disabled={!file}
                className="analyze-button"
              >
                Analyze Project
              </button>
              {status === 'error' && (
                <div className="error-message">{statusMessage}</div>
              )}
            </div>
          </div>
        ) : status === 'uploading' || status === 'processing' ? (
          <div className="processing-section">
            <div className="spinner"></div>
            <h2>Analyzing Project</h2>
            <p>{statusMessage}</p>
          </div>
        ) : status === 'completed' && results ? (
          <div className="results-section">
            <div className="controls">
              <button 
                onClick={() => setViewMode('overview')}
                className={viewMode === 'overview' ? 'active' : ''}
              >
                Overview
              </button>
              <button 
                onClick={() => setViewMode('graph')}
                className={viewMode === 'graph' ? 'active' : ''}
              >
                Call Graph
              </button>
              <button 
                onClick={() => setViewMode('contracts')}
                className={viewMode === 'contracts' ? 'active' : ''}
              >
                Contracts
              </button>
              <button 
                onClick={() => {
                  setStatus('idle');
                  setResults(null);
                  setFile(null);
                  setSelectedNode(null);
                  setHighlightExternalCalls(false);
                }}
                className="new-analysis"
              >
                New Analysis
              </button>
            </div>

            {viewMode === 'overview' && (
              <div className="overview">
                {renderSummary()}
                {renderReentrancyPatterns()}
              </div>
            )}

            {viewMode === 'graph' && (
              <div className="graph-section">
                <div className="graph-controls">
                  <label className="checkbox-label">
                    <input 
                      type="checkbox" 
                      checked={highlightExternalCalls}
                      onChange={(e) => setHighlightExternalCalls(e.target.checked)}
                    />
                    Highlight External Calls
                  </label>
                  {selectedNode && (
                    <div className="selected-node-info">
                      Selected: {selectedNode}
                      <button onClick={() => {
                        setSelectedNode(null);
                        setHighlightExternalCalls(false);
                      }}>Clear</button>
                    </div>
                  )}
                  <select 
                    value={selectedContract || 'all'} 
                    onChange={(e) => setSelectedContract(e.target.value === 'all' ? null : e.target.value)}
                  >
                    <option value="all">All Contracts</option>
                    {results.contracts.map((contract) => (
                      <option key={contract.name} value={contract.name}>
                        {contract.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="graph-container">
                  <CallGraph 
                    data={results.call_graph}
                    onNodeClick={handleNodeClick}
                    selectedNode={selectedNode}
                    highlightExternalCalls={highlightExternalCalls}
                    selectedContract={selectedContract}
                  />
                </div>
                <div className="compact-legend">
                  <span className="legend-item">
                    <span className="legend-color" style={{background: '#44ff44'}}></span>Public/External
                  </span>
                  <span className="legend-item">
                    <span className="legend-color" style={{background: '#8888ff'}}></span>Internal/Private
                  </span>
                  <span className="legend-item">
                    <span className="legend-color" style={{background: '#ff4444'}}></span>External Call
                  </span>
                  <span className="legend-item">
                    <span className="legend-color" style={{background: '#ff8844'}}></span>Cross-Contract
                  </span>
                </div>
              </div>
            )}

            {viewMode === 'contracts' && (
              <div className="contracts-view">
                <div className="sidebar">
                  {renderContractList()}
                </div>
                <div className="main-content">
                  {renderFunctionList()}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default App;
