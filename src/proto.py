#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import csv
import json
import argparse
import logging
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
        self.csv_file = self.version_dir / self.config["csv_filename"]
        
        self.definitions: List[Dict] = []
        self.imports: List[str] = []
        self.package_name: str = ""
        self.syntax: str = ""
        
        self.all_type_names: Set[str] = set()
        self.type_dependencies: Dict[str, Set[str]] = {}
        
        self.cmd_messages: List[Dict] = []
        self.data_messages: List[Dict] = []
        self.enum_definitions: List[Dict] = []
        
        self._setup_logging()
        
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"配置加载完成")
            return config
        except FileNotFoundError:
            print(f"配置文件不存在，使用默认配置")
            return self._get_default_config()
        except json.JSONDecodeError as e:
            print(f"配置文件格式错误，使用默认配置")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        return {
            "input_proto_file": "all-in-one.proto",
            "version": "v5.0.0",
            "output_directory": "output",
            "protocol_subdirectory": "protocol",
            "csv_filename": "protocol.csv",
            "proto_file_extension": ".proto",
            "encoding": {
                "input": ["utf-8", "gbk", "latin-1"],
                "output": "utf-8"
            },
            "csv_format": {
                "header": True,
                "delimiter": ",",
                "fields": ["message_name", "cmd_id"]
            },
            "parsing": {
                "preserve_comments": True,
                "include_syntax": True,
                "include_package": True,
                "include_imports": True,
                "cmd_id_patterns": [
                    r"//\s*CmdId:\s*(\d+)",
                    r"//\s*(\d+)",
                    r"//\s*cmd:\s*(\d+)",
                    r"//\s*CMD_ID:\s*(\d+)"
                ],
                "message_name_pattern": r"message\s+(\w+)\s*\{",
                "enum_name_pattern": r"enum\s+(\w+)\s*\{",
                "request_suffixes": ["Req", "Request"],
                "response_suffixes": ["Rsp", "Response", "Resp"],
                "notify_suffixes": ["Notify", "Ntf"],
                "data_suffixes": ["Data", "Info", "Config"]
            },
            "output": {
                "create_empty_files": False,
                "overwrite_existing": True,
                "log_level": "INFO"
            }
        }
    
    def _setup_logging(self):
        log_level = getattr(logging, self.config["output"]["log_level"], logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def _read_file_with_encoding(self, filepath: str) -> str:
        encodings = self.config["encoding"]["input"]
        
        for encoding in encodings:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    content = f.read()
                return content
            except (UnicodeDecodeError, UnicodeError):
                continue
        
        raise ValueError(f"无法使用任何编码读取文件: {filepath}")
    
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
        
        self._parse_definitions(content)
        self._classify_definitions()
        self._analyze_dependencies()
        
        messages_count = len([d for d in self.definitions if d['type'] == 'message'])
        enums_count = len([d for d in self.definitions if d['type'] == 'enum'])
        cmd_count = len(self.cmd_messages)
        
        print(f"解析完成: {messages_count} 个message, {enums_count} 个enum, {cmd_count} 个有CmdId")
    
    def _parse_definitions(self, content: str) -> None:
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        lines = content.split('\n')
        
        i = 0
        current_cmd_id = None
        current_comments = []
        
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('//'):
                current_comments.append(line)
                cmd_id = self._extract_cmd_id(line)
                if cmd_id:
                    current_cmd_id = cmd_id
                i += 1
                continue
            
            message_pattern = self.config["parsing"]["message_name_pattern"]
            message_match = re.search(message_pattern, line)
            if message_match:
                definition = self._parse_single_definition(lines, i, 'message', current_cmd_id, current_comments)
                if definition:
                    self.definitions.append(definition)
                    self.all_type_names.add(definition['name'])
                    i = definition['end_line']
                    current_cmd_id = None
                    current_comments = []
                    continue
            
            enum_pattern = self.config["parsing"]["enum_name_pattern"]
            enum_match = re.search(enum_pattern, line)
            if enum_match:
                definition = self._parse_single_definition(lines, i, 'enum', current_cmd_id, current_comments)
                if definition:
                    self.definitions.append(definition)
                    self.all_type_names.add(definition['name'])
                    i = definition['end_line']
                    current_cmd_id = None
                    current_comments = []
                    continue
            
            if line and not line.startswith('//'):
                if not any(pattern in line for pattern in ['message', 'enum', 'import', 'syntax', 'package']):
                    current_comments = []
            
            i += 1
    
    def _extract_cmd_id(self, comment_line: str) -> Optional[str]:
        cmd_patterns = self.config["parsing"]["cmd_id_patterns"]
        
        for pattern in cmd_patterns:
            match = re.search(pattern, comment_line, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _parse_single_definition(self, lines: List[str], start_line: int, def_type: str, 
                                cmd_id: Optional[str], comments: List[str]) -> Optional[Dict]:
        line = lines[start_line].strip()
        
        if def_type == 'message':
            pattern = self.config["parsing"]["message_name_pattern"]
        else:
            pattern = self.config["parsing"]["enum_name_pattern"]
        
        match = re.search(pattern, line)
        if not match:
            return None
        
        name = match.group(1)
        body, end_line = self._parse_definition_body(lines, start_line)
        full_definition = f"{def_type} {name} {{\n{body}\n}}"
        message_type = self._identify_message_type(name, def_type)
        description = self._extract_description(comments)
        
        return {
            'name': name,
            'type': def_type,
            'cmd_id': cmd_id,
            'body': body,
            'full_definition': full_definition,
            'comments': comments,
            'line_number': start_line + 1,
            'end_line': end_line,
            'message_type': message_type,
            'description': description
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
    
    def _extract_description(self, comments: List[str]) -> str:
        if not comments:
            return ""
        
        descriptions = []
        for comment in comments:
            clean_comment = re.sub(r'^//\s*', '', comment)
            clean_comment = re.sub(r'CmdId:\s*\d+', '', clean_comment, flags=re.IGNORECASE)
            clean_comment = re.sub(r'^\d+\s*$', '', clean_comment)
            clean_comment = clean_comment.strip()
            
            if clean_comment and clean_comment not in descriptions:
                descriptions.append(clean_comment)
        
        return ' | '.join(descriptions)
    
    def _classify_definitions(self) -> None:
        for definition in self.definitions:
            if definition['type'] == 'enum':
                self.enum_definitions.append(definition)
            elif definition['cmd_id']:
                self.cmd_messages.append(definition)
            else:
                self.data_messages.append(definition)
    
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
        
        body = '\n'.join(body_lines).strip()
        return body, i
    
    def _analyze_dependencies(self) -> None:
        for definition in self.definitions:
            deps = set()
            body = definition['body']
            
            # 更全面的类型模式匹配，包括map类型
            type_patterns = [
                r'(\w+)\s+\w+\s*=',  # 基本字段类型
                r'repeated\s+(\w+)\s+\w+\s*=',  # repeated字段
                r'optional\s+(\w+)\s+\w+\s*=',  # optional字段
                r'required\s+(\w+)\s+\w+\s*=',  # required字段
                r'map<\s*\w+\s*,\s*(\w+)\s*>\s+\w+\s*=',  # map值类型
                r'map<\s*(\w+)\s*,\s*\w+\s*>\s+\w+\s*=',  # map键类型
                r'map<\s*(\w+)\s*,\s*(\w+)\s*>\s+\w+\s*=',  # map键值类型
            ]
            
            for pattern in type_patterns:
                matches = re.findall(pattern, body)
                for match in matches:
                    if isinstance(match, tuple):
                        # 处理map<K,V>的情况，match是元组
                        for type_name in match:
                            if type_name in self.all_type_names:
                                deps.add(type_name)
                    else:
                        # 处理单个类型的情况
                        if match in self.all_type_names:
                            deps.add(match)
            
            self.type_dependencies[definition['name']] = deps
    
    def _get_definition_imports(self, definition_name: str) -> List[str]:
        imports = []
        deps = self.type_dependencies.get(definition_name, set())
        
        for dep in deps:
            import_statement = f'import "{dep}.proto";'
            imports.append(import_statement)
        
        return sorted(imports)
    
    def create_output_structure(self) -> None:
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self.protocol_dir.mkdir(parents=True, exist_ok=True)
        print(f"输出目录: {self.version_dir}")
    
    def generate_proto_files(self) -> None:
        extension = self.config["proto_file_extension"]
        output_encoding = self.config["encoding"]["output"]
        overwrite = self.config["output"]["overwrite_existing"]
        
        for definition in self.definitions:
            filename = f"{definition['name']}{extension}"
            filepath = self.protocol_dir / filename
            
            if filepath.exists() and not overwrite:
                continue
            
            content_parts = []
            
            if definition['cmd_id']:
                content_parts.append(f"// {definition['name']} - CmdId: {definition['cmd_id']}")
            else:
                content_parts.append(f"// {definition['name']} - {definition['message_type']}")
            
            if definition['description']:
                content_parts.append(f"// {definition['description']}")
            
            content_parts.append("")
            
            if self.syntax:
                content_parts.append(self.syntax)
                content_parts.append("")
            
            if self.package_name:
                content_parts.append(f"package {self.package_name};")
                content_parts.append("")
            
            specific_imports = self._get_definition_imports(definition['name'])
            if specific_imports:
                content_parts.extend(specific_imports)
                content_parts.append("")
            
            if self.imports:
                for imp in self.imports:
                    if imp not in specific_imports:
                        content_parts.append(imp)
                if any(imp not in specific_imports for imp in self.imports):
                    content_parts.append("")
            
            if definition['comments'] and self.config["parsing"]["preserve_comments"]:
                content_parts.extend(definition['comments'])
            
            content_parts.append(definition['full_definition'])
            
            with open(filepath, 'w', encoding=output_encoding) as f:
                f.write('\n'.join(content_parts))
        
        print(f"生成 {len(self.definitions)} 个proto文件")
    
    def generate_csv_report(self) -> None:
        csv_config = self.config["csv_format"]
        output_encoding = self.config["encoding"]["output"]
        
        with open(self.csv_file, 'w', newline='', encoding=output_encoding) as f:
            writer = csv.writer(f, delimiter=csv_config["delimiter"])
            
            # 写入表头（如果配置要求）
            if csv_config["header"]:
                writer.writerow(csv_config["fields"])
            
            # 只写入有CmdId的消息，按CmdId排序
            cmd_definitions = [d for d in self.definitions if d['cmd_id']]
            cmd_definitions.sort(key=lambda x: int(x['cmd_id']))
            
            for definition in cmd_definitions:
                row = []
                for field in csv_config["fields"]:
                    if field == "message_name":
                        row.append(definition['name'])
                    elif field == "cmd_id":
                        row.append(definition['cmd_id'])
                    else:
                        row.append(definition.get(field, ''))
                writer.writerow(row)
        
        print(f"生成CSV: {self.csv_file} (包含 {len(cmd_definitions)} 个有CmdId的消息)")
    
    def run(self) -> None:
        try:
            print("开始处理原神Proto文件")
            print(f"输入文件: {self.input_file}")
            print(f"版本: {self.version}")
            
            if not os.path.exists(self.input_file):
                raise FileNotFoundError(f"输入文件不存在: {self.input_file}")
            
            print("解析proto文件...")
            self.parse_proto_file()
            
            print("创建输出目录...")
            self.create_output_structure()
            
            print("生成proto文件...")
            self.generate_proto_files()
            
            print("生成CSV报告...")
            self.generate_csv_report()
            
            print("处理完成！")
            
            self._print_final_statistics()
            
        except Exception as e:
            print(f"处理过程中发生错误: {e}")
            raise
    
    def _print_final_statistics(self) -> None:
        print("\n" + "="*40)
        print("统计信息")
        print("="*40)
        print(f"版本: {self.version}")
        print(f"输出目录: {self.version_dir}")
        print(f"总定义: {len(self.definitions)}")
        print(f"有CmdId消息: {len(self.cmd_messages)}")
        print(f"数据结构: {len(self.data_messages)}")
        print(f"枚举: {len(self.enum_definitions)}")
        
        if self.cmd_messages:
            cmd_ids = [int(d['cmd_id']) for d in self.cmd_messages]
            print(f"CmdId范围: {min(cmd_ids)} - {max(cmd_ids)}")
        
        print("="*40)


def create_default_config() -> None:
    splitter = GenshinProtoSplitter()
    config = splitter._get_default_config()
    
    with open("config.json", 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print("已创建默认配置文件: config.json")


def main():
    parser = argparse.ArgumentParser(description="原神Proto文件分割工具")
    
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("-i", "--input", help="输入proto文件路径")
    parser.add_argument("-o", "--output", help="输出目录路径")
    parser.add_argument("-v", "--version", help="版本标识")
    parser.add_argument("--create-config", action="store_true", help="创建默认配置文件")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], 
                       default="INFO", help="日志级别")
    
    args = parser.parse_args()
    
    if args.create_config:
        create_default_config()
        return
    
    try:
        splitter = GenshinProtoSplitter(args.config)
        
        if args.input:
            splitter.input_file = args.input
            splitter.config["input_proto_file"] = args.input
        
        if args.output:
            splitter.config["output_directory"] = args.output
            splitter.output_dir = args.output
            splitter.version_dir = Path(args.output) / splitter.version
            splitter.protocol_dir = splitter.version_dir / splitter.config["protocol_subdirectory"]
            splitter.csv_file = splitter.version_dir / splitter.config["csv_filename"]
        
        if args.version:
            splitter.config["version"] = args.version
            splitter.version = args.version
            splitter.version_dir = Path(splitter.output_dir) / args.version
            splitter.protocol_dir = splitter.version_dir / splitter.config["protocol_subdirectory"]
            splitter.csv_file = splitter.version_dir / splitter.config["csv_filename"]
        
        if args.log_level:
            splitter.config["output"]["log_level"] = args.log_level
            splitter._setup_logging()
        
        if not splitter.input_file:
            print("错误: 未指定输入文件")
            return 1
        
        splitter.run()
        return 0
        
    except KeyboardInterrupt:
        print("\n用户中断操作")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())