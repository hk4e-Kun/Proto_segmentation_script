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
        self.all_message_names: Set[str] = set()  # 存储所有消息名称
        
        # 预编译正则表达式
        self.message_pattern = re.compile(self.config["parsing"]["message_name_pattern"])
        self.enum_pattern = re.compile(self.config["parsing"]["enum_name_pattern"])
        self.field_pattern = re.compile(r'^\s*(?:repeated\s+|optional\s+|required\s+)?(\w+)\s+(\w+)\s*=\s*\d+;')
        self.cmd_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.config["parsing"]["cmd_id_patterns"]]
        
        # 新增：用于检测需要导入的类型的正则表达式
        self.type_pattern = re.compile(r'^\s*(?:repeated\s+|optional\s+|required\s+)?(?:map\s*<\s*\w+\s*,\s*)?(\w+)(?:\s*>)?\s+(\w+)\s*=\s*\d+;')
        self.map_pattern = re.compile(r'^\s*(?:repeated\s+|optional\s+|required\s+)?map\s*<\s*(\w+)\s*,\s*(\w+)\s*>\s+(\w+)\s*=\s*\d+;')
        
        # 缓存
        self.obfuscated_cache: Dict[str, bool] = {}
        
    def _load_config(self, config_file: str) -> Dict[str, Any]:
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"配置文件加载失败: {e}")
            raise
    
    def _read_file_with_encoding(self, filepath: str) -> str:
        for encoding in self.config["encoding"]["input"]:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法读取文件: {filepath}")
    
    def _is_obfuscated_field(self, name: str) -> bool:
        """判断是否为混淆字段（缓存结果）"""
        if name in self.obfuscated_cache:
            return self.obfuscated_cache[name]
        
        min_consecutive = self.config.get("obfuscation", {}).get("min_consecutive_uppercase", 5)
        
        # 检查排除模式
        exclude_patterns = self.config.get("obfuscation", {}).get("exclude_patterns", [])
        for pattern in exclude_patterns:
            if re.search(pattern, name):
                self.obfuscated_cache[name] = False
                return False
        
        # 检查包含模式
        include_patterns = self.config.get("obfuscation", {}).get("include_patterns", [])
        if include_patterns:
            for pattern in include_patterns:
                if re.search(pattern, name):
                    self.obfuscated_cache[name] = True
                    return True
        
        # 检查连续大写字母
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
    
    def _has_obfuscated_fields(self, body: str) -> bool:
        """检查消息体中是否有混淆字段"""
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
        """注释掉消息体中的混淆字段"""
        lines = body.split('\n')
        result_lines = []
        
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('//'):
                result_lines.append(line)
                continue
            
            # 尝试匹配map类型
            map_match = self.map_pattern.match(stripped)
            if map_match:
                key_type = map_match.group(1)
                value_type = map_match.group(2)
                field_name = map_match.group(3)
                
                # 检查字段类型和名称是否混淆
                if (self._is_obfuscated_field(key_type) or 
                    self._is_obfuscated_field(value_type) or 
                    self._is_obfuscated_field(field_name)):
                    
                    result_lines.append(f"    // {stripped}  // 混淆字段")
                else:
                    result_lines.append(line)
            else:
                # 匹配普通字段
                field_match = self.field_pattern.match(stripped)
                if field_match:
                    field_type = field_match.group(1)
                    field_name = field_match.group(2)
                    
                    # 检查字段类型和名称是否混淆
                    if (self._is_obfuscated_field(field_type) or 
                        self._is_obfuscated_field(field_name)):
                        
                        result_lines.append(f"    // {stripped}  // 混淆字段")
                    else:
                        result_lines.append(line)
                else:
                    result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    def _comment_out_definition(self, definition_text: str) -> str:
        """将定义的每一行都注释掉"""
        lines = definition_text.split('\n')
        commented_lines = []
        
        for line in lines:
            if line.strip():
                commented_lines.append(f"// {line}")
            else:
                commented_lines.append("//")
        
        return '\n'.join(commented_lines)
    
    def _extract_required_imports(self, body: str) -> Set[str]:
        """提取消息体中需要导入的类型"""
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
            
            # 检查map类型
            map_match = self.map_pattern.match(line)
            if map_match:
                key_type = map_match.group(1)
                value_type = map_match.group(2)
                
                # 检查key类型
                if (key_type not in basic_types and 
                    key_type[0].isupper() and 
                    key_type in self.all_message_names):
                    required_types.add(key_type)
                
                # 检查value类型
                if (value_type not in basic_types and 
                    value_type[0].isupper() and 
                    value_type in self.all_message_names):
                    required_types.add(value_type)
                continue
            
            # 检查普通字段类型
            type_match = self.type_pattern.match(line)
            if type_match:
                field_type = type_match.group(1)
                
                # 判断是否需要导入：
                # 1. 不是基本类型
                # 2. 首字母大写（按照你的要求）
                # 3. 在所有消息名称中存在
                if (field_type not in basic_types and 
                    field_type[0].isupper() and 
                    field_type in self.all_message_names):
                    required_types.add(field_type)
        
        return required_types
    
    def parse_proto_file(self) -> None:
        print("开始解析proto文件...")
        content = self._read_file_with_encoding(self.input_file)
        
        # 解析头部信息
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
        
        # 移除块注释
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        
        # 第一次遍历：收集所有消息名称
        self._collect_all_message_names(content)
        
        # 第二次遍历：解析定义
        self._parse_definitions(content)
        
        print(f"解析完成，找到 {len(self.definitions)} 个定义")
    
    def _collect_all_message_names(self, content: str) -> None:
        """收集所有消息和枚举名称"""
        # 查找所有message定义
        message_matches = re.findall(r'^\s*message\s+(\w+)\s*\{', content, re.MULTILINE)
        for name in message_matches:
            self.all_message_names.add(name)
        
        # 查找所有enum定义
        enum_matches = re.findall(r'^\s*enum\s+(\w+)\s*\{', content, re.MULTILINE)
        for name in enum_matches:
            self.all_message_names.add(name)
        
        print(f"收集到 {len(self.all_message_names)} 个消息/枚举名称")
    
    def _parse_definitions(self, content: str) -> None:
        lines = content.split('\n')
        i = 0
        current_cmd_id = None
        current_comments = []
        
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('//'):
                current_comments.append(line)
                # 提取cmd_id
                for pattern in self.cmd_patterns:
                    match = pattern.search(line)
                    if match:
                        current_cmd_id = match.group(1)
                        break
                i += 1
                continue
            
            # 解析message
            if self.message_pattern.search(line):
                definition = self._parse_single_definition(lines, i, 'message', current_cmd_id, current_comments)
                if definition:
                    self.definitions.append(definition)
                    i = definition['end_line']
                    current_cmd_id = None
                    current_comments = []
                    continue
            
            # 解析enum
            if self.enum_pattern.search(line):
                definition = self._parse_single_definition(lines, i, 'enum', current_cmd_id, current_comments)
                if definition:
                    self.definitions.append(definition)
                    i = definition['end_line']
                    current_cmd_id = None
                    current_comments = []
                    continue
            
            # 清空无关注释
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
        
        # 判断是否为混淆消息
        is_obfuscated_message = self._is_obfuscated_field(name)
        has_obfuscated_fields = self._has_obfuscated_fields(body)
        
        # 处理混淆字段
        if has_obfuscated_fields or is_obfuscated_message:
            body = self._comment_out_obfuscated_fields(body)
        
        # 提取需要导入的类型
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
        
        # 找到开始大括号
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
        
        # 解析body内容
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
        print(f"创建输出目录: {self.protocol_dir}")
    
    def generate_proto_files(self) -> None:
        print("开始生成proto文件...")
        extension = self.config["proto_file_extension"]
        output_encoding = self.config["encoding"]["output"]
        overwrite = self.config["output"]["overwrite_existing"]
        
        split_count = 0
        skip_count = 0
        commented_count = 0
        
        # 创建名称到定义的映射，用于快速查找
        name_to_definition = {d['name']: d for d in self.definitions}
        
        for definition in self.definitions:
            # 跳过混淆消息（按配置）
            if definition['is_obfuscated']:
                if not self.config.get("obfuscation", {}).get("split_obfuscated_by_default", False):
                    skip_count += 1
                    continue
            
            filename = f"{definition['name']}{extension}"
            filepath = self.protocol_dir / filename
            
            if filepath.exists() and not overwrite:
                continue
            
            content_parts = []
            
            # 添加头部注释
            if definition['cmd_id']:
                comment = f"// {definition['name']} - CmdId: {definition['cmd_id']}"
                if definition['is_obfuscated']:
                    comment += " (混淆字段)"
                content_parts.append(comment)
            else:
                comment = f"// {definition['name']} - {definition['message_type']}"
                if definition['is_obfuscated']:
                    comment += " (混淆字段)"
                content_parts.append(comment)
            
            content_parts.append("")
            
            # 添加syntax
            if self.syntax:
                content_parts.append(self.syntax)
                content_parts.append("")
            
            # 添加Java package选项
            proto_config = self.config.get("proto_format", {})
            if proto_config.get("add_java_package_option", True):
                java_package = proto_config.get("java_package", "emu.grasscutter.net.proto")
                content_parts.append(f'option java_package = "{java_package}";')
                content_parts.append("")
            
            # 添加package
            if self.package_name:
                content_parts.append(f"package {self.package_name};")
                content_parts.append("")
            
            # 添加全局imports
            if self.imports:
                content_parts.extend(self.imports)
                content_parts.append("")
            
            # 添加特定的导入（增强：处理混淆字段）
            if definition.get('required_imports'):
                import_list = sorted(definition['required_imports'])
                
                for import_type in import_list:
                    import_filename = f"{import_type}{extension}"
                    
                    # 检查导入的类型是否是混淆的
                    is_obfuscated_import = False
                    if import_type in name_to_definition:
                        import_def = name_to_definition[import_type]
                        is_obfuscated_import = import_def['is_obfuscated']
                    
                    # 根据混淆状态决定是否注释
                    if is_obfuscated_import and self.config.get("obfuscation", {}).get("comment_obfuscated_definitions", True):
                        content_parts.append(f'// import "{import_filename}"; // 混淆类型')
                    else:
                        content_parts.append(f'import "{import_filename}";')
                
                if import_list:
                    content_parts.append("")
                    print(f"为 {definition['name']} 添加导入: {', '.join(import_list)}")
            
            # 添加注释
            if definition['comments'] and self.config["parsing"]["preserve_comments"]:
                content_parts.extend(definition['comments'])
            
            # 添加定义内容
            if definition['is_obfuscated'] and self.config.get("obfuscation", {}).get("comment_obfuscated_definitions", True):
                commented_definition = self._comment_out_definition(definition['full_definition'])
                content_parts.append(commented_definition)
                commented_count += 1
            else:
                content_parts.append(definition['full_definition'])
            
            # 写入文件
            try:
                with open(filepath, 'w', encoding=output_encoding) as f:
                    f.write('\n'.join(content_parts))
                split_count += 1
            except Exception as e:
                print(f"写入文件失败 {filepath}: {e}")
        
        print(f"分割了 {split_count} 个proto文件")
        print(f"跳过了 {skip_count} 个混淆字段")
        print(f"注释了 {commented_count} 个混淆字段定义")
    
    def generate_java_opcodes(self) -> None:
        """生成Java格式的PacketOpcodes.java文件"""
        if not self.config.get("output", {}).get("generate_java_opcodes", True):
            return
        
        print("生成Java opcodes文件...")
        output_encoding = self.config["encoding"]["output"]
        java_config = self.config.get("java_format", {})
        
        try:
            with open(self.java_file, 'w', encoding=output_encoding) as f:
                # 包声明
                if java_config.get("include_package", False):
                    package_name = java_config.get("package_name", "emu.grasscutter.net.packet")
                    f.write(f"package {package_name};\n\n")
                
                # 类声明
                class_name = java_config.get("class_name", "PacketOpcodes")
                access_modifier = java_config.get("access_modifier", "public")
                f.write(f"{access_modifier} final class {class_name} {{\n")
                
                # 获取所有CMD消息
                cmd_definitions = [d for d in self.definitions if d['cmd_id']]
                
                # 排序
                if self.config.get("output", {}).get("sort_opcodes_by_id", True):
                    cmd_definitions.sort(key=lambda x: int(x['cmd_id']))
                else:
                    cmd_definitions.sort(key=lambda x: x['name'])
                
                # 写入字段
                field_modifier = java_config.get("field_modifier", "public static final int")
                for definition in cmd_definitions:
                    if definition['is_obfuscated']:
                        f.write(f"    // {field_modifier} {definition['name']} = {definition['cmd_id']}; // 混淆字段\n")
                    else:
                        f.write(f"    {field_modifier} {definition['name']} = {definition['cmd_id']};\n")
                
                f.write("}\n")
            
            print(f"生成Java文件: {self.java_file}")
        except Exception as e:
            print(f"生成Java文件失败: {e}")
    
    def run(self) -> None:
        if not os.path.exists(self.input_file):
            raise FileNotFoundError(f"输入文件不存在: {self.input_file}")
        
        print(f"输入文件: {self.input_file}")
        print(f"输出目录: {self.version_dir}")
        
        self.parse_proto_file()
        self.create_output_structure()
        self.generate_proto_files()
        self.generate_java_opcodes()
        
        print("\n=== 处理完成 ===")
        print(f"总共处理了 {len(self.definitions)} 个定义")
        
        # 统计信息
        cmd_count = sum(1 for d in self.definitions if d['cmd_id'])
        enum_count = sum(1 for d in self.definitions if d['is_enum'])
        obfuscated_count = sum(1 for d in self.definitions if d['is_obfuscated'])
        
        print(f"CMD消息: {cmd_count} 个")
        print(f"枚举定义: {enum_count} 个")
        print(f"混淆字段: {obfuscated_count} 个")


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
        print("\n用户中断操作")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


if __name__ == "__main__":
    exit(main())