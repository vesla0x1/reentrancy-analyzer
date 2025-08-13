import os
import shutil
import tempfile
import subprocess
import zipfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import traceback

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from analyzer import MultiContractAnalyzer

app = FastAPI(title="Solidity Reentrancy Analyzer API", version="1.0.0")
analysis_cache = {}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalysisStatus(BaseModel):
    id: str
    status: str
    message: Optional[str] = None
    timestamp: str


class AnalysisResult(BaseModel):
    id: str
    status: str
    contracts: List[Dict]
    functions: List[Dict]
    call_graph: Dict
    cfg: Dict
    reentrancy_patterns: List[Dict]
    summary: Dict
    timestamp: str


class NodeInfo(BaseModel):
    id: str
    type: str
    function_name: str
    contract_name: str
    called_function: Optional[str] = None
    is_external: bool = False
    modifies_state: bool = False


class EdgeInfo(BaseModel):
    source: str
    target: str
    type: str
    label: Optional[str] = None


def extract_zip(file_path: str, extract_to: str) -> bool:
    """Extract uploaded zip file"""
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_to)
        return True
    except Exception as e:
        print(f"Error extracting zip: {e}")
        return False


def run_crytic_compile(project_path: str) -> Optional[str]:
    """Run crytic-compile on the project"""
    try:
        result = subprocess.run(
            ["crytic-compile", project_path],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            print(f"Crytic compile error: {result.stderr}")
            return None

        crytic_output = Path(project_path) / "crytic-export"
        if crytic_output.exists():
            return str(crytic_output)

        build_info = Path(project_path) / "out" / "build-info"
        if build_info.exists():
            return str(build_info)

        return None

    except subprocess.TimeoutExpired:
        print("Crytic compile timeout")
        return None

    except Exception as e:
        print(f"Error running crytic-compile: {e}")
        return None


def convert_graph_for_frontend(analyzer: MultiContractAnalyzer) -> Dict:
    """Convert NetworkX graphs to frontend-friendly format"""
    nodes = []
    edges = []

    for node_id in analyzer.global_call_graph.nodes():
        node_data = analyzer.global_call_graph.nodes[node_id]

        if node_id.startswith("EXTERNAL:"):
            node_type = "external"
            label = node_id.replace("EXTERNAL:", "")
            contract = "External"
            function = label
        elif node_id.startswith("INHERITED:"):
            node_type = "inherited"
            label = node_id.replace("INHERITED:", "")
            parts = label.split(".")
            contract = parts[0] if len(parts) > 0 else "Unknown"
            function = parts[1] if len(parts) > 1 else label
        else:
            parts = node_id.split(".")
            contract = parts[0] if len(parts) > 0 else "Unknown"
            function = parts[1] if len(parts) > 1 else node_id
            visibility = node_data.get('visibility', 'internal')

            if visibility in ['public', 'external']:
                node_type = "public"
            else:
                node_type = "internal"

            label = node_id

        nodes.append({
            "id": node_id,
            "label": label,
            "type": node_type,
            "contract": contract,
            "function": function,
            "visibility": node_data.get('visibility', 'unknown'),
            "state_mutability": node_data.get('state_mutability', ''),
            "has_state_changes": len(node_data.get('state_changes', [])) > 0,
            "external_calls_count": len(node_data.get('external_calls', [])),
        })

    for source, target, data in analyzer.global_call_graph.edges(data=True):
        edges.append({
            "source": source,
            "target": target,
            "type": data.get('call_type', 'internal'),
            "is_resolved": data.get('is_resolved', False),
            "via_interface": data.get('via_interface'),
        })

    return {
        "nodes": nodes,
        "edges": edges
    }


def convert_cfg_for_frontend(analyzer: MultiContractAnalyzer) -> Dict:
    """Convert CFG to frontend format grouped by function"""
    cfg_by_function = {}

    for node_id in analyzer.cfg.nodes():
        node_data = analyzer.cfg.nodes[node_id].get('cfg_node')
        if node_data:
            func_name = node_data.function_name
            if func_name not in cfg_by_function:
                cfg_by_function[func_name] = {
                    "nodes": [],
                    "edges": []
                }

            cfg_by_function[func_name]["nodes"].append({
                "id": node_id,
                "type": node_data.node_type.value,
                "function_name": func_name,
                "called_function": node_data.called_function,
                "is_external": node_data.is_external,
                "modifies_state": node_data.modifies_state,
            })

    for source, target, data in analyzer.cfg.edges(data=True):
        for func_name in cfg_by_function:
            safe_func_name = func_name.replace('.', '_')
            if source.startswith(safe_func_name) or target.startswith(safe_func_name):
                cfg_by_function[func_name]["edges"].append({
                    "source": source,
                    "target": target,
                    "label": data.get("label", "")
                })
                break

    return cfg_by_function


def analyze_project(analysis_id: str, project_path: str):
    """Background task to analyze the project"""
    try:
        analysis_cache[analysis_id] = {
            "status": "compiling",
            "message": "Running crytic-compile...",
            "timestamp": datetime.now().isoformat()
        }

        build_info_path = run_crytic_compile(project_path)
        if not build_info_path:
            raise Exception("Failed to compile project with crytic-compile")

        analysis_cache[analysis_id]["status"] = "analyzing"
        analysis_cache[analysis_id]["message"] = "Analyzing contracts..."

        analyzer = MultiContractAnalyzer()
        contexts = analyzer.load_build_info(build_info_path)

        if not contexts:
            raise Exception("No contracts found in build output")

        analyzer.analyze_contracts(contexts)

        call_graph = convert_graph_for_frontend(analyzer)
        cfg = convert_cfg_for_frontend(analyzer)

        contracts = []
        for name, data in analyzer.all_contracts.items():
            contracts.append({
                "name": name,
                "type": "interface" if data.get("is_interface") else "contract",
                "functions_count": len(data.get("functions", {})),
                "state_variables_count": len(data.get("state_variables", [])),
                "is_abstract": data.get("is_abstract", False),
                "base_contracts": data.get("base_contracts", []),
                "file_path": data.get("file_path", "")
            })

        functions = []
        for name, data in analyzer.all_functions.items():
            functions.append({
                "name": name,
                "contract": data.get("contract"),
                "visibility": data.get("visibility"),
                "state_mutability": data.get("state_mutability"),
                "external_calls": len(data.get("external_calls", [])),
                "state_changes": len(data.get("state_changes", [])),
                "is_override": data.get("is_override", False)
            })

        patterns = []
        for pattern in analyzer.reentrancy_patterns:
            patterns.append({
                "type": pattern.get("type"),
                "function": pattern.get("function"),
                "severity": pattern.get("severity"),
                "classification": pattern.get("classification"),
                "details": pattern.get("details"),
                "external_call_target": pattern.get("external_call_target"),
                "state_changes_count": len(pattern.get("state_changes_after", []))
            })

        summary = {
            "total_contracts": len(analyzer.all_contracts),
            "total_functions": len(analyzer.all_functions),
            "external_calls": len(analyzer.external_calls),
            "cross_contract_calls": len(analyzer.cross_contract_calls),
            "reentrancy_patterns": len(analyzer.reentrancy_patterns),
            "critical_issues": len([p for p in patterns if p["severity"] == "critical"]),
            "high_issues": len([p for p in patterns if p["severity"] == "high"]),
            "medium_issues": len([p for p in patterns if p["severity"] == "medium"]),
            "low_issues": len([p for p in patterns if p["severity"] == "low"])
        }

        analysis_cache[analysis_id] = {
            "status": "completed",
            "contracts": contracts,
            "functions": functions,
            "call_graph": call_graph,
            "cfg": cfg,
            "reentrancy_patterns": patterns,
            "summary": summary,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        print(f"Analysis error: {traceback.format_exc()}")
        analysis_cache[analysis_id] = {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }
    finally:
        if os.path.exists(project_path):
            shutil.rmtree(project_path, ignore_errors=True)


@app.get("/")
async def root():
    return {"message": "Solidity Reentrancy Analyzer API", "version": "1.0.0"}


@app.post("/analyze")
async def analyze_zip(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """Upload and analyze a Foundry project zip file"""
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="File must be a ZIP archive")

    analysis_id = str(uuid.uuid4())
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, "project.zip")
    extract_path = os.path.join(temp_dir, "project")

    try:
        with open(zip_path, "wb") as f:
            content = await file.read()
            f.write(content)

        if not extract_zip(zip_path, extract_path):
            raise HTTPException(status_code=400, detail="Failed to extract ZIP file")

        project_root = extract_path
        foundry_toml = Path(extract_path).rglob("foundry.toml")
        for toml in foundry_toml:
            project_root = str(toml.parent)
            break

        background_tasks.add_task(analyze_project, analysis_id, project_root)

        return {
            "analysis_id": analysis_id,
            "status": "processing",
            "message": "Analysis started"
        }

    except Exception as e:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analysis/{analysis_id}")
async def get_analysis(analysis_id: str):
    """Get analysis results"""
    if analysis_id not in analysis_cache:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return analysis_cache[analysis_id]


@app.get("/analysis/{analysis_id}/status")
async def get_analysis_status(analysis_id: str):
    """Get analysis status"""
    if analysis_id not in analysis_cache:
        raise HTTPException(status_code=404, detail="Analysis not found")

    result = analysis_cache[analysis_id]
    return {
        "id": analysis_id,
        "status": result.get("status"),
        "message": result.get("message"),
        "timestamp": result.get("timestamp")
    }


@app.get("/analysis/{analysis_id}/graph/{function_name}")
async def get_function_graph(analysis_id: str, function_name: str):
    """Get call graph for a specific function"""
    if analysis_id not in analysis_cache:
        raise HTTPException(status_code=404, detail="Analysis not found")

    result = analysis_cache[analysis_id]
    if result.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Analysis not completed")

    call_graph = result.get("call_graph", {})
    cfg = result.get("cfg", {})

    function_cfg = cfg.get(function_name, {"nodes": [], "edges": []})

    related_nodes = set()
    related_edges = []

    for edge in call_graph.get("edges", []):
        if edge["source"] == function_name or edge["target"] == function_name:
            related_nodes.add(edge["source"])
            related_nodes.add(edge["target"])
            related_edges.append(edge)

    related_node_data = [
        node for node in call_graph.get("nodes", [])
        if node["id"] in related_nodes
    ]

    return {
        "function": function_name,
        "call_graph": {
            "nodes": related_node_data,
            "edges": related_edges
        },
        "cfg": function_cfg
    }


@app.delete("/analysis/{analysis_id}")
async def delete_analysis(analysis_id: str):
    """Delete analysis results"""
    if analysis_id not in analysis_cache:
        raise HTTPException(status_code=404, detail="Analysis not found")

    del analysis_cache[analysis_id]
    return {"message": "Analysis deleted"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
