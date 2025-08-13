import json
import networkx as nx
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class NodeType(Enum):
    FUNCTION_CALL = "FunctionCall"
    EXTERNAL_CALL = "ExternalCall"
    STATE_CHANGE = "StateChange"
    CONDITION = "Condition"
    LOOP = "Loop"
    RETURN = "Return"
    REVERT = "Revert"
    MODIFIER = "Modifier"
    ENTRY = "Entry"
    EXIT = "Exit"
    INHERITED_CALL = "InheritedCall"
    INDIRECT_CALL = "IndirectCall"
    KNOWN_EXTERNAL_CALL = "KnownExternalCall"


@dataclass
class CFGNode:
    id: str
    node_type: NodeType
    source_location: Optional[Dict] = None
    function_name: Optional[str] = None
    called_function: Optional[str] = None
    contract_name: Optional[str] = None
    is_external: bool = False
    is_inherited: bool = False
    modifies_state: bool = False
    ast_node: Optional[Dict] = None
    predecessors: Set[str] = field(default_factory=set)
    successors: Set[str] = field(default_factory=set)


@dataclass
class ContractContext:
    """Context for multi-contract analysis"""
    contract_name: str
    file_path: str
    ast_data: Dict
    is_interface: bool = False
    is_library: bool = False
    is_abstract: bool = False


class MultiContractAnalyzer:
    def __init__(self):
        self.global_call_graph = nx.DiGraph()
        self.cfg = nx.DiGraph()
        self.all_functions = {}
        self.all_contracts = {}
        self.external_calls = []
        self.state_variables = {}
        self.reentrancy_patterns = []
        self.node_counter = 0
        self.inheritance_map = {}
        self.indirect_calls = []
        self.contract_contexts = {}
        self.interface_implementations = {}
        self.known_safe_contracts = set()
        self.cross_contract_calls = []

    def load_build_info(self, build_info_path: str) -> List[ContractContext]:
        """Load all contracts from Crytic compile build info"""
        contexts = []

        build_dir = Path(build_info_path)
        for json_file in build_dir.glob("*.json"):
            with open(json_file, 'r') as f:
                build_data = json.load(f)
                contexts.extend(self._extract_contracts_from_build(build_data, str(json_file)))

        return contexts

    def _extract_contracts_from_build(self, build_data: Dict, file_path: str) -> List[ContractContext]:
        """Extract contract contexts from build data"""
        contexts = []

        if 'output' in build_data:
            contracts_data = build_data['output'].get('contracts', {})
            sources_data = build_data['output'].get('sources', {})

            # Get AST for each source file
            for source_file, source_info in sources_data.items():
                if 'ast' in source_info:
                    ast = source_info['ast']
                    # Extract contracts from this AST
                    contract_contexts = self._extract_contracts_from_ast(ast, source_file, file_path)
                    contexts.extend(contract_contexts)

        elif 'nodeType' in build_data and build_data['nodeType'] == 'SourceUnit':
            contract_contexts = self._extract_contracts_from_ast(build_data, file_path, file_path)
            contexts.extend(contract_contexts)

        return contexts

    def _extract_contracts_from_ast(self, ast_node: Dict, source_file: str, build_file: str) -> List[ContractContext]:
        """Extract contract contexts from an AST node"""
        contexts = []

        if ast_node.get('nodeType') == 'SourceUnit':
            for node in ast_node.get('nodes', []):
                if node.get('nodeType') == 'ContractDefinition':
                    contract_name = node.get('name', '')
                    is_interface = node.get('contractKind') == 'interface'
                    is_library = node.get('contractKind') == 'library'
                    is_abstract = node.get('abstract', False)

                    context = ContractContext(
                        contract_name=contract_name,
                        file_path=source_file,
                        ast_data=node,
                        is_interface=is_interface,
                        is_library=is_library,
                        is_abstract=is_abstract
                    )
                    contexts.append(context)

        return contexts

    def analyze_contracts(self, contexts: List[ContractContext]):
        """Analyze multiple contracts with cross-contract analysis"""
        print(f"Analyzing {len(contexts)} contracts...")

        for context in contexts:
            self.contract_contexts[context.contract_name] = context
            self._extract_contract_info(context)

        self._build_inheritance_map()
        self._identify_interface_implementations()

        for context in contexts:
            if not context.is_interface:
                self._build_contract_call_graph(context)

        for context in contexts:
            if not context.is_interface:
                self._build_contract_cfg(context)

        self._detect_cross_contract_reentrancy()

        print("Analysis complete:")
        print(f"  - {len(self.all_contracts)} contracts")
        print(f"  - {len(self.all_functions)} functions")
        print(f"  - {len(self.external_calls)} external calls")
        print(f"  - {len(self.cross_contract_calls)} cross-contract calls")
        print(f"  - {len(self.reentrancy_patterns)} potential reentrancy patterns")

    def _extract_contract_info(self, context: ContractContext):
        """Extract contract information from AST"""
        contract_name = context.contract_name
        node = context.ast_data

        self.all_contracts[contract_name] = {
            'functions': {},
            'state_variables': [],
            'modifiers': {},
            'ast_node': node,
            'base_contracts': [],
            'is_interface': context.is_interface,
            'is_library': context.is_library,
            'is_abstract': context.is_abstract,
            'file_path': context.file_path
        }

        self.state_variables[contract_name] = set()

        base_contracts = node.get('baseContracts', [])
        for base in base_contracts:
            base_name = self._extract_base_contract_name(base)
            if base_name:
                self.all_contracts[contract_name]['base_contracts'].append(base_name)

        self._extract_contract_members(node, contract_name)

        print(f"  Extracted {contract_name}: {len(self.all_contracts[contract_name]['functions'])} functions")

    def _extract_base_contract_name(self, base_node: Dict) -> Optional[str]:
        """Extract base contract name from inheritance node"""
        base_name = base_node.get('baseName', {})
        if isinstance(base_name, dict):
            if base_name.get('nodeType') == 'UserDefinedTypeName':
                path_node = base_name.get('pathNode', base_name)
                return path_node.get('name', '')
            elif base_name.get('nodeType') == 'IdentifierPath':
                return base_name.get('name', '')
        return None

    def _extract_contract_members(self, node: Dict, contract_name: str):
        """Extract functions, state variables, and modifiers from contract"""
        for child in node.get('nodes', []):
            node_type = child.get('nodeType', '')

            if node_type == 'FunctionDefinition' and child.get('kind') == 'function':
                self._extract_function(child, contract_name)
            elif node_type == 'VariableDeclaration' and child.get('stateVariable', False):
                self._extract_state_variable(child, contract_name)
            elif node_type == 'ModifierDefinition':
                self._extract_modifier(child, contract_name)

    def _extract_function(self, node: Dict, contract_name: str):
        """Extract function information"""
        func_name = node.get('name', '')
        if not func_name:
            return

        visibility = node.get('visibility', 'internal')
        state_mutability = node.get('stateMutability', '')
        is_virtual = node.get('virtual', False)
        is_override = node.get('overrides', []) != [] or node.get('override', False)
        full_func_name = f"{contract_name}.{func_name}"

        self.all_functions[full_func_name] = {
            'name': func_name,
            'contract': contract_name,
            'visibility': visibility,
            'state_mutability': state_mutability,
            'ast_node': node,
            'calls': [],
            'external_calls': [],
            'cross_contract_calls': [],
            'indirect_calls': [],
            'state_changes': [],
            'is_virtual': is_virtual,
            'is_override': is_override
        }

        self.all_contracts[contract_name]['functions'][func_name] = self.all_functions[full_func_name]

    def _extract_state_variable(self, node: Dict, contract_name: str):
        """Extract state variable information"""
        var_name = node.get('name', '')
        var_type = node.get('typeDescriptions', {}).get('typeString', '')

        self.state_variables[contract_name].add(var_name)

        self.all_contracts[contract_name]['state_variables'].append({
            'name': var_name,
            'type': var_type,
            'ast_node': node
        })

    def _extract_modifier(self, node: Dict, contract_name: str):
        """Extract modifier information"""
        modifier_name = node.get('name', '')
        self.all_contracts[contract_name]['modifiers'][modifier_name] = {
            'name': modifier_name,
            'ast_node': node
        }

    def _build_inheritance_map(self):
        """Build complete inheritance map"""
        for contract_name, contract_data in self.all_contracts.items():
            self.inheritance_map[contract_name] = contract_data['base_contracts']

    def _identify_interface_implementations(self):
        """Identify which contracts implement which interfaces"""
        for contract_name, contract_data in self.all_contracts.items():
            if contract_data['is_interface']:
                interface_functions = set(contract_data['functions'].keys())

                for other_contract, other_data in self.all_contracts.items():
                    if other_contract != contract_name and not other_data['is_interface']:
                        if interface_functions.issubset(set(other_data['functions'].keys())):
                            self.interface_implementations.setdefault(contract_name, []).append(other_contract)

                        if contract_name in other_data.get('base_contracts', []):
                            if contract_name not in self.interface_implementations:
                                self.interface_implementations[contract_name] = []
                            if other_contract not in self.interface_implementations[contract_name]:
                                self.interface_implementations[contract_name].append(other_contract)

    def _build_contract_call_graph(self, context: ContractContext):
        """Build call graph for a contract with cross-contract awareness"""
        contract_name = context.contract_name

        for func_name, func_data in self.all_contracts[contract_name]['functions'].items():
            full_func_name = f"{contract_name}.{func_name}"
            self.global_call_graph.add_node(full_func_name, **func_data)
            self._analyze_function_calls_multi(func_data['ast_node'], full_func_name)

    def _analyze_function_calls_multi(self, node: Dict, current_function: str, parent_node=None):
        """Analyze function calls with multi-contract awareness"""
        if not isinstance(node, dict):
            return

        node_type = node.get('nodeType', '')

        if node_type == 'FunctionCall':
            self._process_function_call_multi(node, current_function)
        elif node_type == 'MemberAccess':
            member_name = node.get('memberName', '')
            if member_name in ['encodeWithSelector', 'encode', 'encodePacked']:
                self._check_for_indirect_call_multi(node, current_function, parent_node)
        elif node_type == 'Assignment':
            left = node.get('leftHandSide', {}) or node.get('left', {})
            if self._is_state_variable_access_multi(left, current_function.split('.')[0]):
                self.all_functions[current_function]['state_changes'].append({
                    'type': 'assignment',
                    'variable': self._get_full_variable_path(left),
                    'node': node
                })

        for key, child in node.items():
            if isinstance(child, dict):
                self._analyze_function_calls_multi(child, current_function, node)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        self._analyze_function_calls_multi(item, current_function, node)

    def _process_function_call_multi(self, call_node: Dict, current_function: str):
        """Process function calls with cross-contract resolution"""
        expression = call_node.get('expression', {})
        current_contract = current_function.split('.')[0]

        call_info = self._classify_call(expression, current_contract)

        if call_info['is_cross_contract']:
            target_contract = call_info.get('implementation_contract') or call_info.get('target_contract')
            target_function = call_info.get('target_function')

            if target_contract and target_function:
                full_target = f"{target_contract}.{target_function}"

                if full_target in self.all_functions:
                    self.cross_contract_calls.append({
                        'from': current_function,
                        'to': full_target,
                        'ast_node': call_node,
                        'via_interface': call_info.get('target_contract') if call_info.get('implementation_contract') else None
                    })

                    self.all_functions[current_function]['cross_contract_calls'].append({
                        'target': full_target,
                        'contract': target_contract,
                        'function': target_function,
                        'interface': call_info.get('target_contract') if call_info.get('implementation_contract') else None
                    })

                    self.global_call_graph.add_edge(
                        current_function, full_target,
                        call_type='cross_contract',
                        is_resolved=True,
                        via_interface=call_info.get('target_contract') if call_info.get('implementation_contract') else None
                    )
                else:
                    self._add_external_call(current_function, call_info['called_function'], call_node)
        elif call_info['is_external']:
            # External call to unknown contract
            self._add_external_call(current_function, call_info['called_function'], call_node)
        else:
            self._process_internal_call(current_function, call_info, call_node)

    def _classify_call(self, expression: Dict, current_contract: str) -> Dict:
        """Classify a function call"""
        result = {
            'is_external': False,
            'is_cross_contract': False,
            'is_inherited': False,
            'called_function': 'unknown',
            'target_contract': None,
            'target_function': None,
            'implementation_contract': None,
        }

        expr_type = expression.get('nodeType', '')

        if expr_type == 'MemberAccess':
            base_expr = expression.get('expression', {})
            member_name = expression.get('memberName', '')
            result['called_function'] = member_name
            result['target_function'] = member_name

            type_desc = expression.get('typeDescriptions', {})
            type_string = type_desc.get('typeString', '')

            if base_expr.get('nodeType') == 'Identifier':
                base_type_desc = base_expr.get('typeDescriptions', {})
                base_type_string = base_type_desc.get('typeString', '')

                if base_expr.get('name') == 'super':
                    result['is_inherited'] = True
                    return result

                if 'contract' in base_type_string:
                    # Extract contract name from type string
                    contract_match = self._extract_contract_from_type(base_type_string)
                    if contract_match:
                        result['target_contract'] = contract_match

                        # Find concrete implementation if it's an interface
                        impl_contract = self._find_implementation(
                            contract_match,
                            member_name
                        )
                        if impl_contract:
                            result['implementation_contract'] = impl_contract

                        # It's cross-contract if the target contract is different from current contract
                        if contract_match != current_contract:
                            result['is_cross_contract'] = True
                            result['is_external'] = True
                        else:
                            result['is_external'] = False

            if 'external' in type_string and 'function' in type_string:
                result['is_external'] = True

        elif expr_type == 'Identifier':
            func_name = expression.get('name', '')
            result['called_function'] = func_name
            result['target_function'] = func_name

        return result

    def _extract_contract_from_type(self, type_string: str) -> Optional[str]:
        """Extract contract name from type string"""
        if 'contract' in type_string:
            parts = type_string.split()
            for i, part in enumerate(parts):
                if part == 'contract' and i + 1 < len(parts):
                    return parts[i + 1]
        return None

    def _find_implementation(self, interface_name: str, function_name: str) -> Optional[str]:
        """Find a concrete implementation of an interface function"""
        if interface_name in self.all_contracts:
            contract_data = self.all_contracts[interface_name]

            if contract_data.get('is_interface'):
                implementations = self.interface_implementations.get(interface_name, [])

                for impl_contract in implementations:
                    if function_name in self.all_contracts[impl_contract].get('functions', {}):
                        return impl_contract

            elif not contract_data.get('is_abstract'):
                return interface_name

        return None

    def _add_external_call(self, from_function: str, target: str, ast_node: Dict):
        """Add an external call"""
        call_info = {
            'caller': from_function,
            'called_function': target,
            'is_external': True,
            'ast_node': ast_node
        }

        self.external_calls.append(call_info)
        self.all_functions[from_function]['external_calls'].append(call_info)

        external_node = f"EXTERNAL:{target}"
        self.global_call_graph.add_node(external_node, type='external', name=target)
        self.global_call_graph.add_edge(from_function, external_node, call_type='external')

    def _process_internal_call(self, current_function: str, call_info: Dict, ast_node: Dict):
        """Process internal or inherited calls"""
        current_contract = current_function.split('.')[0]
        target_function = call_info['target_function']

        if call_info['is_inherited']:
            inherited_target = self._find_inherited_function(target_function, current_contract)
            if inherited_target:
                self.global_call_graph.add_edge(
                    current_function, inherited_target,
                    call_type='inherited'
                )
        else:
            full_target = f"{current_contract}.{target_function}"
            if full_target in self.all_functions:
                self.global_call_graph.add_edge(
                    current_function, full_target,
                    call_type='internal'
                )

    def _find_inherited_function(self, func_name: str, contract: str) -> Optional[str]:
        """Find inherited function implementation"""
        base_contracts = self.inheritance_map.get(contract, [])
        for base in base_contracts:
            full_name = f"{base}.{func_name}"
            if full_name in self.all_functions:
                return full_name
            inherited = self._find_inherited_function(func_name, base)
            if inherited:
                return inherited
        return None

    def _check_for_indirect_call_multi(self, encode_node: Dict, current_function: str, parent_node: Dict):
        """Check for indirect calls via abi.encode"""
        if parent_node and parent_node.get('nodeType') == 'FunctionCall':
            args = parent_node.get('arguments', [])
            if args:
                first_arg = args[0]
                if first_arg.get('nodeType') == 'MemberAccess' and first_arg.get('memberName') == 'selector':
                    selector_base = first_arg.get('expression', {})
                    if selector_base.get('nodeType') == 'MemberAccess':
                        func_name = selector_base.get('memberName', '')
                        base_expr = selector_base.get('expression', {})

                        is_this_contract = (base_expr.get('nodeType') == 'Identifier' and 
                                          base_expr.get('name') == 'this')

                        if is_this_contract:
                            current_contract = current_function.split('.')[0]
                            target_func = f"{current_contract}.{func_name}"
                            if target_func in self.all_functions:
                                self.global_call_graph.add_edge(
                                    current_function, target_func,
                                    call_type='indirect'
                                )
                                self.indirect_calls.append({
                                    'from': current_function,
                                    'to': target_func
                                })

    def _is_state_variable_access_multi(self, node: Dict, contract_name: str) -> bool:
        """Check if node accesses a state variable"""
        if not isinstance(node, dict):
            return False

        node_type = node.get('nodeType', '')

        if node_type == 'Identifier':
            var_name = node.get('name', '')
            type_desc = node.get('typeDescriptions', {})
            type_string = type_desc.get('typeString', '').lower()

            if 'storage' in type_string:
                return True

            # Check if it's a known state variable
            return var_name in self.state_variables.get(contract_name, set())

        elif node_type == 'MemberAccess':
            type_desc = node.get('typeDescriptions', {})
            type_string = type_desc.get('typeString', '').lower()

            if 'storage' in type_string:
                return True

            base_expr = node.get('expression', {})
            if base_expr.get('nodeType') == 'Identifier':
                base_type = base_expr.get('typeDescriptions', {}).get('typeString', '').lower()
                if 'storage' in base_type:
                    return True

            return self._is_state_variable_access_multi(base_expr, contract_name)

        return False

    def _build_contract_cfg(self, context: ContractContext):
        """Build CFG for all functions in a contract"""
        contract_name = context.contract_name

        for func_name, func_data in self.all_contracts[contract_name]['functions'].items():
            full_func_name = f"{contract_name}.{func_name}"
            self._build_function_cfg(full_func_name, func_data['ast_node'])

    def get_next_node_id(self, func_key: str = None) -> str:
        """Generate unique node ID with function prefix"""
        self.node_counter += 1
        if func_key:
            safe_func_key = func_key.replace('.', '_')
            return f"{safe_func_key}_node_{self.node_counter}"
        return f"node_{self.node_counter}"

    def _build_function_cfg(self, func_key: str, func_node: Dict):
        """Build CFG for a single function"""
        safe_func_key = func_key.replace('.', '_')
        entry_id = f"{safe_func_key}_entry"
        exit_id = f"{safe_func_key}_exit"

        entry_node = CFGNode(entry_id, NodeType.ENTRY, function_name=func_key)
        exit_node = CFGNode(exit_id, NodeType.EXIT, function_name=func_key)

        self.cfg.add_node(entry_id, cfg_node=entry_node)
        self.cfg.add_node(exit_id, cfg_node=exit_node)

        body = func_node.get('body', {})
        if body:
            statements = body.get('statements', [])
            if statements:
                prev_id = entry_id
                for stmt in statements:
                    stmt_id = self._process_statement(stmt, func_key, exit_id)
                    if stmt_id:
                        self.cfg.add_edge(prev_id, stmt_id)
                        prev_id = stmt_id
                if prev_id != entry_id:
                    self.cfg.add_edge(prev_id, exit_id)

    def _process_statement(self, stmt: Dict, func_key: str, exit_id: str) -> Optional[str]:
        """Process a single statement"""
        if not isinstance(stmt, dict):
            return None

        stmt_type = stmt.get('nodeType', '')

        if stmt_type == 'ExpressionStatement':
            expr = stmt.get('expression', {})
            node_id = self.get_next_node_id(func_key)
            return self._process_expression(expr, func_key, node_id)

        elif stmt_type == 'EmitStatement':
            node_id = self.get_next_node_id(func_key)
            cfg_node = CFGNode(node_id, NodeType.CONDITION, function_name=func_key, ast_node=stmt)
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            return node_id

        elif stmt_type == 'VariableDeclarationStatement':
            node_id = self.get_next_node_id(func_key)
            initial_value = stmt.get('initialValue', {})
            if initial_value and initial_value.get('nodeType') == 'FunctionCall':
                return self._process_expression(initial_value, func_key, node_id)
            else:
                cfg_node = CFGNode(node_id, NodeType.CONDITION, function_name=func_key, ast_node=stmt)
                self.cfg.add_node(node_id, cfg_node=cfg_node)
                return node_id

        elif stmt_type == 'Return':
            node_id = self.get_next_node_id(func_key)
            cfg_node = CFGNode(node_id, NodeType.RETURN, function_name=func_key, ast_node=stmt)
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            self.cfg.add_edge(node_id, exit_id)
            return node_id

        elif stmt_type == 'Block':
            statements = stmt.get('statements', [])
            if statements:
                first_id = None
                prev_id = None
                for s in statements:
                    s_id = self._process_statement(s, func_key, exit_id)
                    if s_id:
                        if first_id is None:
                            first_id = s_id
                        if prev_id:
                            self.cfg.add_edge(prev_id, s_id)
                        prev_id = s_id
                return first_id
            return None

        elif stmt_type == 'IfStatement':
            return self._process_if_statement(stmt, func_key, exit_id)

        else:
            node_id = self.get_next_node_id(func_key)
            cfg_node = CFGNode(node_id, NodeType.CONDITION, function_name=func_key, ast_node=stmt)
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            return node_id

    def _process_if_statement(self, stmt: Dict, func_key: str, exit_id: str) -> str:
        """Process if statement with proper branching"""
        condition_id = self.get_next_node_id(func_key)
        condition_node = CFGNode(condition_id, NodeType.CONDITION, function_name=func_key, ast_node=stmt)
        self.cfg.add_node(condition_id, cfg_node=condition_node)

        merge_id = self.get_next_node_id(func_key)
        merge_node = CFGNode(merge_id, NodeType.CONDITION, function_name=func_key)
        self.cfg.add_node(merge_id, cfg_node=merge_node)

        true_branch = stmt.get('trueBody', {})
        if true_branch:
            true_first = self._process_statement(true_branch, func_key, exit_id)
            if true_first:
                self.cfg.add_edge(condition_id, true_first, label='true')
                # Connect end of true branch to merge
                true_last = self._find_branch_end(true_first, func_key, exit_id)
                if true_last and not true_last.endswith('_exit'):
                    self.cfg.add_edge(true_last, merge_id)
        else:
            self.cfg.add_edge(condition_id, merge_id, label='true')

        false_branch = stmt.get('falseBody')
        if false_branch:
            false_first = self._process_statement(false_branch, func_key, exit_id)
            if false_first:
                self.cfg.add_edge(condition_id, false_first, label='false')
                false_last = self._find_branch_end(false_first, func_key, exit_id)
                if false_last and not false_last.endswith('_exit'):
                    self.cfg.add_edge(false_last, merge_id)
        else:
            self.cfg.add_edge(condition_id, merge_id, label='false')

        return condition_id


    def _find_branch_end(self, start_node: str, func_key: str, exit_id: str) -> Optional[str]:
        """Find the last node in a branch that doesn't lead to exit"""
        visited = set()
        last_node = start_node

        def dfs(node):
            nonlocal last_node
            if node in visited or node == exit_id:
                return
            visited.add(node)

            successors = list(self.cfg.successors(node))
            if not successors or all(s == exit_id for s in successors):
                last_node = node
            else:
                for succ in successors:
                    if succ != exit_id:
                        dfs(succ)

        dfs(start_node)
        return last_node

    def _process_expression(self, expr: Dict, func_key: str, node_id: str) -> Optional[str]:
        """Process an expression"""
        if not isinstance(expr, dict):
            return None

        expr_type = expr.get('nodeType', '')

        if expr_type == 'FunctionCall':
            expression = expr.get('expression', {})
            contract_name = func_key.split('.')[0]
            call_info = self._classify_call(expression, contract_name)

            if call_info['is_cross_contract']:
                target_contract = call_info.get('implementation_contract') or call_info.get('target_contract')
                if target_contract:
                    node_type = NodeType.KNOWN_EXTERNAL_CALL
                    called_func = f"{target_contract}.{call_info['target_function']}"
                else:
                    node_type = NodeType.EXTERNAL_CALL
                    called_func = call_info['called_function']
            elif call_info['is_external']:
                node_type = NodeType.EXTERNAL_CALL
                called_func = call_info['called_function']
            elif call_info['is_inherited']:
                node_type = NodeType.INHERITED_CALL
                called_func = call_info['called_function']
            else:
                node_type = NodeType.FUNCTION_CALL
                called_func = call_info['called_function']

            cfg_node = CFGNode(
                node_id, node_type,
                function_name=func_key,
                called_function=called_func,
                is_external=call_info['is_external'] or call_info['is_cross_contract'],
                is_inherited=call_info['is_inherited'],
                ast_node=expr
            )
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            return node_id

        elif expr_type == 'Assignment':
            left_side = expr.get('leftHandSide', {}) or expr.get('left', {})
            contract_name = func_key.split('.')[0]
            is_state_change = self._is_state_variable_access_multi(left_side, contract_name)

            cfg_node = CFGNode(
                node_id, NodeType.STATE_CHANGE,
                function_name=func_key,
                modifies_state=is_state_change,
                ast_node=expr
            )
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            return node_id

        elif expr_type == 'BinaryOperation':
            left = expr.get('leftExpression', {})
            right = expr.get('rightExpression', {})

            if left.get('nodeType') == 'FunctionCall':
                return self._process_expression(left, func_key, node_id)
            elif right.get('nodeType') == 'FunctionCall':
                return self._process_expression(right, func_key, node_id)

            cfg_node = CFGNode(node_id, NodeType.CONDITION, function_name=func_key, ast_node=expr)
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            return node_id

        else:
            cfg_node = CFGNode(node_id, NodeType.CONDITION, function_name=func_key, ast_node=expr)
            self.cfg.add_node(node_id, cfg_node=cfg_node)
            return node_id

    def _detect_cross_contract_reentrancy(self):
        """Detect reentrancy patterns with cross-contract analysis"""
        print("\nDetecting cross-contract reentrancy patterns...")
        print("-" * 40)

        for func_key in self.all_functions.keys():
            patterns = self._analyze_function_reentrancy_multi(func_key)
            if patterns:
                print(f"\nFunction: {func_key}")
                for pattern in patterns:
                    print(f"  - {pattern['details']}")
                    print(f"    Severity: {pattern['severity']}")
                    print(f"    Type: {pattern['classification']}")
            self.reentrancy_patterns.extend(patterns)

    def _analyze_function_reentrancy_multi(self, func_key: str) -> List[Dict]:
        """Analyze function for reentrancy with cross-contract context"""
        patterns = []
        safe_func_key = func_key.replace('.', '_')
        func_nodes = [n for n in self.cfg.nodes() if n.startswith(safe_func_key)]
        external_calls_found = []

        for node_id in func_nodes:
            node_data = self.cfg.nodes.get(node_id, {}).get('cfg_node')
            if node_data:
                if node_data.node_type in [NodeType.EXTERNAL_CALL, NodeType.KNOWN_EXTERNAL_CALL]:
                    external_calls_found.append({
                        'node_id': node_id,
                        'called_function': node_data.called_function,
                        'node_data': node_data,
                        'is_known': node_data.node_type == NodeType.KNOWN_EXTERNAL_CALL
                    })

        for ext_call in external_calls_found:
            if ext_call['is_known']:
                target_func = ext_call['called_function']
                if target_func in self.all_functions:
                    can_reenter = self._check_reentrancy_path(target_func, func_key)
                    if can_reenter:
                        state_changes_after = self._find_state_changes_after_node(
                            ext_call['node_id'], safe_func_key
                        )
                        if state_changes_after:
                            patterns.append(self._create_reentrancy_pattern(
                                func_key, ext_call, state_changes_after, 
                                classification='confirmed_reentrancy'
                            ))
                    else:
                        state_changes_after = self._find_state_changes_after_node(
                            ext_call['node_id'], safe_func_key
                        )
                        if state_changes_after:
                            patterns.append(self._create_reentrancy_pattern(
                                func_key, ext_call, state_changes_after,
                                classification='safe_external_call'
                            ))
            else:
                state_changes_after = self._find_state_changes_after_node(
                    ext_call['node_id'], safe_func_key
                )
                if state_changes_after:
                    patterns.append(self._create_reentrancy_pattern(
                        func_key, ext_call, state_changes_after,
                        classification='potential_reentrancy'
                    ))

        return patterns

    def _check_reentrancy_path(self, from_func: str, original_func: str, visited: Set[str] = None) -> bool:
        """Check if there's a path from from_func back to original_func"""
        if visited is None:
            visited = set()

        if from_func in visited:
            return False

        visited.add(from_func)

        for successor in self.global_call_graph.successors(from_func):
            if successor.startswith('EXTERNAL:'):
                return True

            if successor == original_func:
                return True

            if self._check_reentrancy_path(successor, original_func, visited):
                return True

        return False

    def _create_reentrancy_pattern(self, func_key: str, ext_call: Dict, 
                                  state_changes: List, classification: str) -> Dict:
        """Create a reentrancy pattern entry"""
        severity = self._determine_severity(ext_call, state_changes, func_key, classification)

        return {
            'type': classification,
            'function': func_key,
            'external_call_node': ext_call['node_id'],
            'external_call_target': ext_call['called_function'],
            'state_changes_after': state_changes,
            'severity': severity,
            'classification': classification,
            'details': f"External call to {ext_call['called_function']} followed by {len(state_changes)} state changes"
        }

    def _determine_severity(self, ext_call: Dict, state_changes: List, 
                           func_key: str, classification: str) -> str:
        """Determine severity of reentrancy pattern"""
        if classification == 'safe_external_call':
            return 'low'

        if classification == 'confirmed_reentrancy':
            return 'critical'

        func_data = self.all_functions.get(func_key, {})
        visibility = func_data.get('visibility', 'internal')

        if len(state_changes) > 1 and visibility in ['public', 'external']:
            return 'high'
        elif state_changes and visibility in ['public', 'external']:
            return 'medium'
        else:
            return 'low'

    def _find_state_changes_after_node(self, start_node: str, safe_func_key: str) -> List[Dict]:
        """Find state changes after a node"""
        state_changes = []
        visited = set()
        queue = []

        successors = list(self.cfg.successors(start_node))
        for successor in successors:
            if successor not in visited and successor.startswith(safe_func_key):
                queue.append(successor)

        while queue:
            node_id = queue.pop(0)

            if node_id in visited:
                continue

            visited.add(node_id)

            if node_id.endswith('_exit'):
                continue

            node_data = self.cfg.nodes.get(node_id, {}).get('cfg_node')

            if node_data:
                if node_data.node_type == NodeType.STATE_CHANGE and node_data.modifies_state:
                    state_changes.append({
                        'node_id': node_id,
                        'name': self._get_full_variable_path(
                            node_data.ast_node.get('leftHandSide', {})
                        ) if node_data.ast_node else 'unknown'
                    })

            for successor in self.cfg.successors(node_id):
                if successor not in visited and not successor.endswith('_exit'):
                    queue.append(successor)

        return state_changes

    def _get_full_variable_path(self, node: Dict) -> str:
        """Get full path for variable access"""
        if not isinstance(node, dict):
            return ''

        node_type = node.get('nodeType', '')

        if node_type == 'Identifier':
            return node.get('name', '')
        elif node_type == 'MemberAccess':
            base_expr = node.get('expression', {})
            member_name = node.get('memberName', '')

            if base_expr.get('nodeType') == 'Identifier':
                base_name = base_expr.get('name', '')
                return f"{base_name}.{member_name}"
            elif base_expr.get('nodeType') == 'MemberAccess':
                base_path = self._get_full_variable_path(base_expr)
                return f"{base_path}.{member_name}"
            else:
                return member_name

        return ''

    def generate_report(self, output_file: str = "cross_contract_reentrancy_report.txt"):
        """Generate comprehensive analysis report"""
        with open(output_file, 'w') as f:
            f.write("CROSS-CONTRACT REENTRANCY ANALYSIS REPORT\n")
            f.write("=" * 60 + "\n\n")

            f.write(f"Total contracts analyzed: {len(self.all_contracts)}\n")
            f.write(f"Total functions analyzed: {len(self.all_functions)}\n")
            f.write(f"External calls (unknown): {len(self.external_calls)}\n")
            f.write(f"Cross-contract calls (known): {len(self.cross_contract_calls)}\n")
            f.write(f"Indirect calls: {len(self.indirect_calls)}\n")
            f.write(f"Potential reentrancy patterns: {len(self.reentrancy_patterns)}\n\n")

            # Group patterns by classification
            confirmed = [p for p in self.reentrancy_patterns if p['classification'] == 'confirmed_reentrancy']
            potential = [p for p in self.reentrancy_patterns if p['classification'] == 'potential_reentrancy']
            safe = [p for p in self.reentrancy_patterns if p['classification'] == 'safe_external_call']

            if confirmed:
                f.write("CONFIRMED REENTRANCY VULNERABILITIES:\n")
                f.write("-" * 40 + "\n")
                for pattern in confirmed:
                    f.write(f"  Function: {pattern['function']}\n")
                    f.write(f"  External call to: {pattern['external_call_target']}\n")
                    f.write(f"  State changes after: {len(pattern['state_changes_after'])}\n")
                    f.write(f"  Severity: {pattern['severity']}\n\n")

            if potential:
                f.write("\nPOTENTIAL REENTRANCY VULNERABILITIES:\n")
                f.write("-" * 40 + "\n")
                for pattern in potential:
                    f.write(f"  Function: {pattern['function']}\n")
                    f.write(f"  External call to: {pattern['external_call_target']}\n")
                    f.write(f"  State changes after: {len(pattern['state_changes_after'])}\n")
                    f.write(f"  Severity: {pattern['severity']}\n\n")

            if safe:
                f.write("\nSAFE EXTERNAL CALLS (No reentrancy path found):\n")
                f.write("-" * 40 + "\n")
                for pattern in safe:
                    f.write(f"  Function: {pattern['function']}\n")
                    f.write(f"  External call to: {pattern['external_call_target']}\n")
                    f.write(f"  State changes after: {len(pattern['state_changes_after'])}\n\n")

            f.write("\nCROSS-CONTRACT CALL PATHS:\n")
            f.write("-" * 40 + "\n")
            for call in self.cross_contract_calls:  # Limit to first 10
                f.write(f"  {call['from']} -> {call['to']}\n")

        print(f"Report saved to {output_file}")
