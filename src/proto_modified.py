#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import argparse
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any, Set


class GenshinProtoSplitter:
    def __init__(self, config_file: str = "config.json"):
        self.config = self._load_config(config_file)
        self.input_file = self.config.get("input_proto_file", "")
        
        self.output_dir = self.config["output_directory"]
        self.version = self.config["version"]
        self.version_dir = Path(self.output_dir) / self.version
        self.protocol_dir = self.version_dir / self.config["protocol_subdirectory"]
        self.java_file = self.version_dir / self.config.get("java_filename", "PacketOpcodes.java")
        
        self.definitions: List[Dict] = []
        self.imports: List[str] = []
        self.package_name: str = ""
        self.syntax: str = ""
        self.all_message_names: Set[str] = set()
        self.top_level_messages: Set[str] = set()
        # 新增：存储所有将要生成的proto文件名
        self.generated_proto_files: Set[str] = set()
        
        self.message_pattern = re.compile(self.config["parsing"]["message_name_pattern"])
        self.enum_pattern = re.compile(self.config["parsing"]["enum_name_pattern"])
        self.field_pattern = re.compile(r'^\s*(?:repeated\s+|optional\s+|required\s+)?(\w+)\s+(\w+)\s*=\s*\d+;')
        self.cmd_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.config["parsing"]["cmd_id_patterns"]]
        
        self.type_pattern = re.compile(r'^\s*(?:repeated\s+|optional\s+|required\s+)?(?:map\s*<\s*\w+\s*,\s*)?(\w+)(?:\s*>)?\s+(\w+)\s*=\s*\d+;')
        self.map_pattern = re.compile(r'^\s*(?:repeated\s+|optional\s+|required\s+)?map\s*<\s*(\w+)\s*,\s*(\w+)\s*>\s+(\w+)\s*=\s*\d+;')
        
        self.obfuscated_cache: Dict[str, bool] = {}
        
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise
    
    def _read_file_with_encoding(self, filepath: str) -> str:
        for encoding in self.config["encoding"]["input"]:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Cannot read file: {filepath}")
    
    def _is_obfuscated_field(self, name: str) -> bool:
        if name in self.obfuscated_cache:
            return self.obfuscated_cache[name]
        
        min_consecutive = self.config.get("obfuscation", {}).get("min_consecutive_uppercase", 5)
        
        exclude_patterns = self.config.get("obfuscation", {}).get("exclude_patterns", [])
        for pattern in exclude_patterns:
            if re.search(pattern, name):
                self.obfuscated_cache[name] = False
                return False
        
        include_patterns = self.config.get("obfuscation", {}).get("include_patterns", [])
        if include_patterns:
            for pattern in include_patterns:
                if re.search(pattern, name):
                    self.obfuscated_cache[name] = True
                    return True
        
        consecutive_uppercase = 0
        max_consecutive = 0
        
        for char in name:
            if char.isupper():
                consecutive_uppercase += 1
                max_consecutive = max(max_consecutive, consecutive_uppercase)
            else:
                consecutive_uppercase = 0
        
        result = max_consecutive >= min_consecutive
        self.obfuscated_cache[name] = result
        return result
    
    def _has_consecutive_uppercase(self, name: str) -> bool:
        min_consecutive = self.config.get("obfuscation", {}).get("min_consecutive_uppercase", 5)
        consecutive_uppercase = 0
        max_consecutive = 0
        
        for char in name:
            if char.isupper():
                consecutive_uppercase += 1
                max_consecutive = max(max_consecutive, consecutive_uppercase)
            else:
                consecutive_uppercase = 0
        
        return max_consecutive >= min_consecutive
    
    def _is_inside_enum_definition(self, lines: List[str], line_index: int) -> bool:
        brace_count = 0
        for i in range(line_index, -1, -1):
            line = lines[i].strip()
            if not line or line.startswith('//'):
                continue
            
            brace_count += line.count('}') - line.count('{')
            
            if re.match(r'^\s*enum\s+\w+\s*\{', line):
                return brace_count <= 0
            
            if re.match(r'^\s*message\s+\w+\s*\{', line):
                return False
        
        return False
    
    def _is_inside_oneof_definition(self, lines: List[str], line_index: int) -> bool:
        """判断当前行是否在oneof定义内部"""
        brace_count = 0
        for i in range(line_index, -1, -1):
            line = lines[i].strip()
            if not line or line.startswith('//'):
                continue
            
            brace_count += line.count('}') - line.count('{')
            
            if re.match(r'^\s*oneof\s+\w+\s*\{', line):
                return brace_count <= 0
            
            if re.match(r'^\s*(message|enum)\s+\w+\s*\{', line):
                return False
        
        return False
    
    def _is_line_commented(self, line: str) -> bool:
        return line.strip().startswith('//')
    
    def _find_message_block_bounds(self, lines: List[str], start_line: int) -> Tuple[int, int]:
        brace_count = 0
        in_message = False
        
        for i in range(start_line, len(lines)):
            line = lines[i]
            
            if not in_message and '{' in line:
                in_message = True
                brace_count += line.count('{')
                brace_count -= line.count('}')
            elif in_message:
                brace_count += line.count('{')
                brace_count -= line.count('}')
                
                if brace_count == 0:
                    return start_line, i
        
        return start_line, len(lines) - 1
    
    def _process_generated_proto_content(self, content: str) -> str:
        lines = content.split('\n')
        result_lines = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            original_line = line
            stripped = line.strip()
            
            if not stripped or stripped.startswith('//'):
                result_lines.append(line)
                i += 1
                continue
            
            message_match = re.match(r'^(\s*)(message|enum)\s+(\w+)\s*\{', line)
            if message_match:
                indent = message_match.group(1)
                def_type = message_match.group(2)
                name = message_match.group(3)
                
                # 如果message/enum名称是混淆字段，跳过不注释该行
                if self._is_obfuscated_field(name):
                    result_lines.append(line)  # 保留原行不注释
                else:
                    result_lines.append(line)
                i += 1
                continue
            
            import_match = re.match(r'^(\s*)import\s+"([^"]+)";', line)
            if import_match:
                indent = import_match.group(1)
                import_file = import_match.group(2)
                type_name = import_file.replace('.proto', '')
                
                if self._is_obfuscated_field(type_name):
                    result_lines.append(f"{indent}// {line.strip()}")
                else:
                    result_lines.append(line)
                i += 1
                continue
            
            # 处理oneof定义
            if re.match(r'^\s*oneof\s+\w+\s*\{', stripped):
                # oneof定义行本身不做混淆判断，直接保留
                result_lines.append(line)
                i += 1
                
                # 处理oneof内部字段
                brace_count = 1
                while i < len(lines) and brace_count > 0:
                    current_line = lines[i]
                    current_stripped = current_line.strip()
                    
                    # 计算花括号平衡
                    brace_count += current_stripped.count('{') - current_stripped.count('}')
                    
                    if brace_count == 0:
                        # oneof结束花括号
                        result_lines.append(current_line)
                        i += 1
                        break
                    
                    # 检查oneof内部字段是否混淆
                    if (current_stripped and 
                        not current_stripped.startswith('//') and 
                        not current_stripped.startswith('/*') and
                        not current_stripped.startswith('*') and
                        '=' in current_stripped and
                        current_stripped.endswith(';')):
                        
                        # 检查是否为混淆字段
                        field_match = self.field_pattern.match(current_stripped)
                        if field_match:
                            field_type = field_match.group(1)
                            field_name = field_match.group(2)
                            
                            if (self._is_obfuscated_field(field_type) or 
                                self._is_obfuscated_field(field_name)):
                                # 注释掉混淆字段
                                indent = current_line[:len(current_line) - len(current_line.lstrip())]
                                result_lines.append(f"{indent}// {current_stripped}")
                            else:
                                result_lines.append(current_line)
                        else:
                            result_lines.append(current_line)
                    else:
                        result_lines.append(current_line)
                    
                    i += 1
                continue
            
            if self._is_inside_enum_definition(lines, i):
                result_lines.append(line)
                i += 1
                continue
            
            if '=' in stripped and stripped.endswith(';'):
                indent = line[:len(line) - len(line.lstrip())]
                
                map_match = self.map_pattern.match(stripped)
                if map_match:
                    key_type = map_match.group(1)
                    value_type = map_match.group(2)
                    field_name = map_match.group(3)
                    
                    if (self._is_obfuscated_field(key_type) or 
                        self._is_obfuscated_field(value_type) or 
                        self._is_obfuscated_field(field_name)):
                        result_lines.append(f"{indent}// {stripped}")
                    else:
                        result_lines.append(line)
                    i += 1
                    continue
                
                field_match = self.field_pattern.match(stripped)
                if field_match:
                    field_type = field_match.group(1)
                    field_name = field_match.group(2)
                    
                    if (self._is_obfuscated_field(field_type) or 
                        self._is_obfuscated_field(field_name)):
                        result_lines.append(f"{indent}// {stripped}")
                    else:
                        result_lines.append(line)
                    i += 1
                    continue
            
            words = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', stripped)
            has_obfuscated_word = False
            
            for word in words:
                if self._is_obfuscated_field(word):
                    has_obfuscated_word = True
                    break
            
            if has_obfuscated_word:
                indent = line[:len(line) - len(line.lstrip())]
                result_lines.append(f"{indent}// {stripped}")
            else:
                result_lines.append(line)
            
            i += 1
        
        return '\n'.join(result_lines)
    
    def _process_proto_content(self, content: str) -> str:
        lines = content.split('\n')
        result_lines = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            if not stripped or stripped.startswith('//'):
                result_lines.append(line)
                i += 1
                continue
            
            # 处理oneof定义
            if re.match(r'^\s*oneof\s+\w+\s*\{', stripped):
                # oneof定义行本身不做混淆判断，直接保留
                result_lines.append(line)
                i += 1
                
                # 处理oneof内部字段
                brace_count = 1
                while i < len(lines) and brace_count > 0:
                    current_line = lines[i]
                    current_stripped = current_line.strip()
                    
                    # 计算花括号平衡
                    brace_count += current_stripped.count('{') - current_stripped.count('}')
                    
                    if brace_count == 0:
                        # oneof结束花括号
                        result_lines.append(current_line)
                        i += 1
                        break
                    
                    # 检查oneof内部字段是否混淆
                    if (current_stripped and 
                        not current_stripped.startswith('//') and 
                        not current_stripped.startswith('/*') and
                        not current_stripped.startswith('*') and
                        '=' in current_stripped and
                        current_stripped.endswith(';')):
                        
                        # 检查map类型
                        map_match = self.map_pattern.match(current_stripped)
                        if map_match:
                            key_type = map_match.group(1)
                            value_type = map_match.group(2)
                            field_name = map_match.group(3)
                            
                            if (self._is_obfuscated_field(key_type) or 
                                self._is_obfuscated_field(value_type) or 
                                self._is_obfuscated_field(field_name)):
                                result_lines.append(f"    // {current_stripped}")
                            else:
                                result_lines.append(current_line)
                        else:
                            # 检查普通字段
                            field_match = self.field_pattern.match(current_stripped)
                            if field_match:
                                field_type = field_match.group(1)
                                field_name = field_match.group(2)
                                
                                if (self._is_obfuscated_field(field_type) or 
                                    self._is_obfuscated_field(field_name)):
                                    result_lines.append(f"    // {current_stripped}")
                                else:
                                    result_lines.append(current_line)
                            else:
                                result_lines.append(current_line)
                    else:
                        result_lines.append(current_line)
                    
                    i += 1
                continue
            
            if self._is_inside_enum_definition(lines, i):
                result_lines.append(line)
                i += 1
                continue
            
            if '=' in stripped and stripped.endswith(';'):
                map_match = self.map_pattern.match(stripped)
                if map_match:
                    key_type = map_match.group(1)
                    value_type = map_match.group(2)
                    field_name = map_match.group(3)
                    
                    if (self._is_obfuscated_field(key_type) or 
                        self._is_obfuscated_field(value_type) or 
                        self._is_obfuscated_field(field_name)):
                        result_lines.append(f"    // {stripped}")
                    else:
                        result_lines.append(line)
                else:
                    field_match = self.field_pattern.match(stripped)
                    if field_match:
                        field_type = field_match.group(1)
                        field_name = field_match.group(2)
                        
                        if (self._is_obfuscated_field(field_type) or 
                            self._is_obfuscated_field(field_name)):
                            result_lines.append(f"    // {stripped}")
                        else:
                            result_lines.append(line)
                    else:
                        result_lines.append(line)
            else:
                result_lines.append(line)
            
            i += 1
        
        return '\n'.join(result_lines)
    
    def _has_obfuscated_fields(self, body: str) -> bool:
        for line in body.split('\n'):
            line = line.strip()
            if not line or line.startswith('//'):
                continue
            
            match = self.field_pattern.match(line)
            if match:
                field_name = match.group(2)
                if self._is_obfuscated_field(field_name):
                    return True
        return False
    
    def _comment_out_obfuscated_fields(self, body: str) -> str:
        lines = body.split('\n')
        result_lines = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            if not stripped or stripped.startswith('//'):
                result_lines.append(line)
                i += 1
                continue
            
            # 处理oneof定义
            if re.match(r'^\s*oneof\s+\w+\s*\{', stripped):
                # oneof定义行本身不做混淆判断，直接保留
                result_lines.append(line)
                i += 1
                
                # 处理oneof内部字段
                brace_count = 1
                while i < len(lines) and brace_count > 0:
                    current_line = lines[i]
                    current_stripped = current_line.strip()
                    
                    # 计算花括号平衡
                    brace_count += current_stripped.count('{') - current_stripped.count('}')
                    
                    if brace_count == 0:
                        # oneof结束花括号
                        result_lines.append(current_line)
                        i += 1
                        break
                    
                    # 检查oneof内部字段是否混淆
                    if (current_stripped and 
                        not current_stripped.startswith('//') and 
                        not current_stripped.startswith('/*') and
                        not current_stripped.startswith('*') and
                        '=' in current_stripped and
                        current_stripped.endswith(';')):
                        
                        # 检查map类型
                        map_match = self.map_pattern.match(current_stripped)
                        if map_match:
                            key_type = map_match.group(1)
                            value_type = map_match.group(2)
                            field_name = map_match.group(3)
                            
                            if (self._is_obfuscated_field(key_type) or 
                                self._is_obfuscated_field(value_type) or 
                                self._is_obfuscated_field(field_name)):
                                result_lines.append(f"    // {current_stripped}")
                            else:
                                result_lines.append(current_line)
                        else:
                            # 检查普通字段
                            field_match = self.field_pattern.match(current_stripped)
                            if field_match:
                                field_type = field_match.group(1)
                                field_name = field_match.group(2)
                                
                                if (self._is_obfuscated_field(field_type) or 
                                    self._is_obfuscated_field(field_name)):
                                    result_lines.append(f"    // {current_stripped}")
                                else:
                                    result_lines.append(current_line)
                            else:
                                result_lines.append(current_line)
                    else:
                        result_lines.append(current_line)
                    
                    i += 1
                continue
            
            enum_value_match = re.match(r'^(\s*)(\w+)\s*=\s*\d+;', stripped)
            if enum_value_match:
                result_lines.append(line)
                i += 1
                continue
            
            map_match = self.map_pattern.match(stripped)
            if map_match:
                key_type = map_match.group(1)
                value_type = map_match.group(2)
                field_name = map_match.group(3)
                
                if (self._is_obfuscated_field(key_type) or 
                    self._is_obfuscated_field(value_type) or 
                    self._is_obfuscated_field(field_name)):
                    
                    result_lines.append(f"    // {stripped}")
                else:
                    result_lines.append(line)
            else:
                field_match = self.field_pattern.match(stripped)
                if field_match:
                    field_type = field_match.group(1)
                    field_name = field_match.group(2)
                    
                    if (self._is_obfuscated_field(field_type) or 
                        self._is_obfuscated_field(field_name)):
                        
                        result_lines.append(f"    // {stripped}")
                    else:
                        result_lines.append(line)
                else:
                    result_lines.append(line)
            
            i += 1
        
        return '\n'.join(result_lines)
    
    def _comment_out_definition(self, definition_text: str) -> str:
        lines = definition_text.split('\n')
        commented_lines = []
        
        for line in lines:
            if line.strip():
                commented_lines.append(f"// {line}")
            else:
                commented_lines.append("//")
        
        return '\n'.join(commented_lines)
    
    def _extract_required_imports(self, body: str) -> Set[str]:
        """修复后的方法：提取所有首字母大写的类型名"""
        required_types = set()
        
        # 基本类型，不需要导入
        basic_types = {
            'int32', 'int64', 'uint32', 'uint64', 'sint32', 'sint64',
            'fixed32', 'fixed64', 'sfixed32', 'sfixed64', 'float', 'double',
            'bool', 'string', 'bytes'
        }
        
        for line in body.split('\n'):
            line = line.strip()
            if not line or line.startswith('//'):
                continue
            
            # 处理 map 类型
            map_match = self.map_pattern.match(line)
            if map_match:
                key_type = map_match.group(1)
                value_type = map_match.group(2)
                
                # 检查 key 类型
                if (key_type not in basic_types and 
                    key_type[0].isupper() and 
                    re.match(r'^[A-Z][a-zA-Z0-9_]*$', key_type)):
                    required_types.add(key_type)
                
                # 检查 value 类型
                if (value_type not in basic_types and 
                    value_type[0].isupper() and 
                    re.match(r'^[A-Z][a-zA-Z0-9_]*$', value_type)):
                    required_types.add(value_type)
                continue
            
            # 处理普通字段类型
            type_match = self.type_pattern.match(line)
            if type_match:
                field_type = type_match.group(1)
                
                # 检查是否是自定义类型（首字母大写，非基本类型）
                if (field_type not in basic_types and 
                    field_type[0].isupper() and 
                    re.match(r'^[A-Z][a-zA-Z0-9_]*$', field_type)):
                    required_types.add(field_type)
        
        return required_types
    
    def _prepare_generated_proto_files(self) -> None:
        """准备将要生成的proto文件名集合"""
        self.generated_proto_files.clear()
        extension = self.config["proto_file_extension"]
        
        for definition in self.definitions:
            # 跳过被混淆的枚举
            if definition['is_enum'] and definition['is_obfuscated']:
                continue
            
            # 跳过被混淆的消息（如果配置不分割混淆消息）
            if not definition['is_enum'] and definition['is_obfuscated']:
                if not self.config.get("obfuscation", {}).get("split_obfuscated_by_default", False):
                    continue
            
            # 添加到将要生成的文件集合
            filename = f"{definition['name']}{extension}"
            self.generated_proto_files.add(filename)
    
    def _validate_imports_against_generated_files(self, required_imports: Set[str]) -> Set[str]:
        """修复后的方法：根据将要生成的proto文件验证导入"""
        valid_imports = set()
        extension = self.config["proto_file_extension"]
        
        for import_type in required_imports:
            proto_filename = f"{import_type}{extension}"
            
            # 检查是否在将要生成的文件列表中
            if proto_filename in self.generated_proto_files:
                valid_imports.add(import_type)
            else:
                # 检查是否已经存在于输出目录中
                proto_file = self.protocol_dir / proto_filename
                if proto_file.exists():
                    valid_imports.add(import_type)
        
        return valid_imports
    
    def parse_proto_file(self) -> None:
        content = self._read_file_with_encoding(self.input_file)
        
        if self.config["parsing"]["include_syntax"]:
            syntax_match = re.search(r'^syntax\s*=\s*"([^"]+)";', content, re.MULTILINE)
            if syntax_match:
                self.syntax = f'syntax = "{syntax_match.group(1)}";'
        
        if self.config["parsing"]["include_package"]:
            package_match = re.search(r'^package\s+([^;]+);', content, re.MULTILINE)
            if package_match:
                self.package_name = package_match.group(1).strip()
        
        if self.config["parsing"]["include_imports"]:
            import_matches = re.findall(r'^import\s+"([^"]+)";', content, re.MULTILINE)
            self.imports = [f'import "{imp}";' for imp in import_matches]
        
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        
        self._collect_all_message_names(content)
        
        self._parse_definitions(content)
        
    def _collect_all_message_names(self, content: str) -> None:
        lines = content.split('\n')
        brace_depth = 0
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('//'):
                continue
            
            if brace_depth == 0:
                message_match = re.match(r'^\s*message\s+(\w+)\s*\{', line)
                if message_match:
                    name = message_match.group(1)
                    self.all_message_names.add(name)
                    self.top_level_messages.add(name)
                
                enum_match = re.match(r'^\s*enum\s+(\w+)\s*\{', line)
                if enum_match:
                    name = enum_match.group(1)
                    self.all_message_names.add(name)
                    self.top_level_messages.add(name)
            else:
                message_match = re.match(r'^\s*message\s+(\w+)\s*\{', line)
                if message_match:
                    name = message_match.group(1)
                    self.all_message_names.add(name)
                
                enum_match = re.match(r'^\s*enum\s+(\w+)\s*\{', line)
                if enum_match:
                    name = enum_match.group(1)
                    self.all_message_names.add(name)
            
            brace_depth += line.count('{') - line.count('}')
    
    def _parse_definitions(self, content: str) -> None:
        lines = content.split('\n')
        i = 0
        current_cmd_id = None
        current_comments = []
        
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('//'):
                current_comments.append(line)
                for pattern in self.cmd_patterns:
                    match = pattern.search(line)
                    if match:
                        current_cmd_id = match.group(1)
                        break
                i += 1
                continue
            
            if self.message_pattern.search(line):
                definition = self._parse_single_definition(lines, i, 'message', current_cmd_id, current_comments)
                if definition:
                    self.definitions.append(definition)
                    i = definition['end_line']
                    current_cmd_id = None
                    current_comments = []
                    continue
            
            if self.enum_pattern.search(line):
                definition = self._parse_single_definition(lines, i, 'enum', current_cmd_id, current_comments)
                if definition:
                    self.definitions.append(definition)
                    i = definition['end_line']
                    current_cmd_id = None
                    current_comments = []
                    continue
            
            if line and not line.startswith('//'):
                if not any(keyword in line for keyword in ['message', 'enum', 'import', 'syntax', 'package']):
                    current_comments = []
            
            i += 1
    
    def _parse_single_definition(self, lines: List[str], start_line: int, def_type: str, 
                                cmd_id: Optional[str], comments: List[str]) -> Optional[Dict]:
        line = lines[start_line].strip()
        
        if def_type == 'message':
            match = self.message_pattern.search(line)
        else:
            match = self.enum_pattern.search(line)
        
        if not match:
            return None
        
        name = match.group(1)
        body, end_line = self._parse_definition_body(lines, start_line)
        
        is_obfuscated_message = self._is_obfuscated_field(name)
        
        if def_type == 'enum':
            has_obfuscated_fields = False
        else:
            has_obfuscated_fields = self._has_obfuscated_fields(body)
        
        if has_obfuscated_fields or (is_obfuscated_message and def_type == 'message'):
            body = self._comment_out_obfuscated_fields(body)
        
        required_imports = set()
        if def_type == 'message':
            required_imports = self._extract_required_imports(body)
        
        full_definition = f"{def_type} {name} {{\n{body}\n}}"
        message_type = self._identify_message_type(name, def_type)
        
        return {
            'name': name,
            'type': def_type,
            'cmd_id': cmd_id,
            'body': body,
            'full_definition': full_definition,
            'comments': comments if def_type != 'enum' else [],
            'line_number': start_line + 1,
            'end_line': end_line,
            'message_type': message_type,
            'is_enum': (def_type == 'enum'),
            'is_obfuscated': is_obfuscated_message,
            'required_imports': required_imports
        }
    
    def _identify_message_type(self, name: str, def_type: str) -> str:
        if def_type == 'enum':
            return 'enum'
        
        parsing_config = self.config["parsing"]
        
        for suffix in parsing_config["request_suffixes"]:
            if name.endswith(suffix):
                return 'request'
        
        for suffix in parsing_config["response_suffixes"]:
            if name.endswith(suffix):
                return 'response'
        
        for suffix in parsing_config["notify_suffixes"]:
            if name.endswith(suffix):
                return 'notify'
        
        for suffix in parsing_config["data_suffixes"]:
            if name.endswith(suffix):
                return 'data'
        
        return 'unknown'
    
    def _parse_definition_body(self, lines: List[str], start_line: int) -> Tuple[str, int]:
        brace_count = 0
        body_lines = []
        i = start_line
        
        while i < len(lines):
            line = lines[i]
            if '{' in line:
                brace_count += line.count('{')
                brace_count -= line.count('}')
                
                brace_pos = line.find('{')
                if brace_pos < len(line) - 1:
                    body_content = line[brace_pos + 1:].rstrip()
                    if body_content:
                        body_lines.append(body_content)
                break
            i += 1
        
        i += 1
        
        while i < len(lines) and brace_count > 0:
            line = lines[i]
            brace_count += line.count('{')
            brace_count -= line.count('}')
            
            if brace_count > 0:
                body_lines.append(line.rstrip())
            elif brace_count == 0:
                closing_brace_pos = line.rfind('}')
                if closing_brace_pos > 0:
                    body_content = line[:closing_brace_pos].rstrip()
                    if body_content:
                        body_lines.append(body_content)
                break
            
            i += 1
        
        return '\n'.join(body_lines).strip(), i
    
    def create_output_structure(self) -> None:
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self.protocol_dir.mkdir(parents=True, exist_ok=True)
    
    def generate_proto_files(self) -> None:
        extension = self.config["proto_file_extension"]
        output_encoding = self.config["encoding"]["output"]
        overwrite = self.config["output"]["overwrite_existing"]
        
        # 首先准备将要生成的proto文件名集合
        self._prepare_generated_proto_files()
        
        split_count = 0
        skip_count = 0
        commented_count = 0
        
        name_to_definition = {d['name']: d for d in self.definitions}
        
        for definition in self.definitions:
            if definition['is_enum'] and definition['is_obfuscated']:
                skip_count += 1
                continue
            
            if not definition['is_enum'] and definition['is_obfuscated']:
                if not self.config.get("obfuscation", {}).get("split_obfuscated_by_default", False):
                    skip_count += 1
                    continue
            
            filename = f"{definition['name']}{extension}"
            filepath = self.protocol_dir / filename
            
            if filepath.exists() and not overwrite:
                continue
            
            content_parts = []
            
            if self.syntax:
                content_parts.append(self.syntax)
                content_parts.append("")
            
            proto_config = self.config.get("proto_format", {})
            if proto_config.get("add_java_package_option", True):
                java_package = proto_config.get("java_package", "emu.grasscutter.net.proto")
                content_parts.append(f'option java_package = "{java_package}";')
                content_parts.append("")
            
            if self.package_name:
                content_parts.append(f"package {self.package_name};")
                content_parts.append("")
            
            if self.imports:
                content_parts.extend(self.imports)
                content_parts.append("")
            
            # 处理导入：现在使用修复后的方法
            if definition.get('required_imports'):
                raw_imports = definition['required_imports']
                valid_imports = self._validate_imports_against_generated_files(raw_imports)
                import_list = sorted(valid_imports)
                
                for import_type in import_list:
                    import_filename = f"{import_type}{extension}"
                    
                    is_obfuscated_import = False
                    if import_type in name_to_definition:
                        import_def = name_to_definition[import_type]
                        is_obfuscated_import = import_def['is_obfuscated']
                    
                    if is_obfuscated_import and self.config.get("obfuscation", {}).get("comment_obfuscated_definitions", True):
                        content_parts.append(f'// import "{import_filename}";')
                    else:
                        content_parts.append(f'import "{import_filename}";')
                
                if import_list:
                    content_parts.append("")
            
            if definition['is_obfuscated'] and self.config.get("obfuscation", {}).get("comment_obfuscated_definitions", True):
                commented_definition = self._comment_out_definition(definition['full_definition'])
                content_parts.append(commented_definition)
                commented_count += 1
            else:
                content_parts.append(definition['full_definition'])
            
            initial_content = '\n'.join(content_parts)
            
            final_content = self._process_generated_proto_content(initial_content)
            
            try:
                with open(filepath, 'w', encoding=output_encoding) as f:
                    f.write(final_content)
                split_count += 1
                
                print(f"Generated: {filename}")
                if initial_content != final_content:
                    print(f"  -> Obfuscated content detected and commented")
                    
            except Exception as e:
                print(f"Write failed {filepath}: {e}")
        
        print(f"\nGenerated: {split_count} files, skipped {skip_count} obfuscated files, commented {commented_count} obfuscated definitions")
    
    def generate_java_opcodes(self) -> None:
        if not self.config.get("output", {}).get("generate_java_opcodes", True):
            return
        
        output_encoding = self.config["encoding"]["output"]
        java_config = self.config.get("java_format", {})
        
        try:
            with open(self.java_file, 'w', encoding=output_encoding) as f:
                if java_config.get("include_package", False):
                    package_name = java_config.get("package_name", "emu.grasscutter.net.packet")
                    f.write(f"package {package_name};\n\n")
                
                class_name = java_config.get("class_name", "PacketOpcodes")
                access_modifier = java_config.get("access_modifier", "public")
                f.write(f"{access_modifier} final class {class_name} {{\n")
                
                cmd_definitions = [d for d in self.definitions if d['cmd_id']]
                
                if self.config.get("output", {}).get("sort_opcodes_by_id", True):
                    cmd_definitions.sort(key=lambda x: int(x['cmd_id']))
                else:
                    cmd_definitions.sort(key=lambda x: x['name'])
                
                field_modifier = java_config.get("field_modifier", "public static final int")
                for definition in cmd_definitions:
                    if definition['is_obfuscated']:
                        f.write(f"    // {field_modifier} {definition['name']} = {definition['cmd_id']};\n")
                    else:
                        f.write(f"    {field_modifier} {definition['name']} = {definition['cmd_id']};\n")
                
                f.write("}\n")
        except Exception as e:
            print(f"Java file generation failed: {e}")
    
    def run(self) -> None:
        if not os.path.exists(self.input_file):
            raise FileNotFoundError(f"Input file not found: {self.input_file}")
        
        self.parse_proto_file()
        self.create_output_structure()
        self.generate_proto_files()
        self.generate_java_opcodes()


def main():
    parser = argparse.ArgumentParser(description="原神Proto文件分割工具")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("-i", "--input", help="输入proto文件路径")
    parser.add_argument("-o", "--output", help="输出目录路径")
    parser.add_argument("-v", "--version", help="版本标识")
    
    args = parser.parse_args()
    
    try:
        splitter = GenshinProtoSplitter(args.config)
        
        # 命令行参数覆盖配置
        if args.input:
            splitter.input_file = args.input
        if args.output:
            splitter.output_dir = args.output
            splitter.version_dir = Path(args.output) / splitter.version
            splitter.protocol_dir = splitter.version_dir / splitter.config["protocol_subdirectory"]
            splitter.java_file = splitter.version_dir / splitter.config.get("java_filename", "PacketOpcodes.java")
        if args.version:
            splitter.version = args.version
            splitter.version_dir = Path(splitter.output_dir) / args.version
            splitter.protocol_dir = splitter.version_dir / splitter.config["protocol_subdirectory"]
            splitter.java_file = splitter.version_dir / splitter.config.get("java_filename", "PacketOpcodes.java")
        
        if not splitter.input_file:
            print("错误: 未指定输入文件")
            return 1
        
        splitter.run()
        return 0
        
    except KeyboardInterrupt:
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


if __name__ == "__main__":
    exit(main())

                    
