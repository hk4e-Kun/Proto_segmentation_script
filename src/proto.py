#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件内容替换脚本
根据配置文件中的规则替换指定文件中的内容
"""

import json
import os
import re
from typing import Dict, List, Tuple
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FileReplacer:
    def __init__(self, config_file: str = "config.json"):
        """
        初始化文件替换器
        
        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.config = self.load_config()
        self.lock = threading.Lock()  # 用于线程安全的日志输出
    
    def load_config(self) -> Dict:
        """
        加载配置文件
        
        Returns:
            配置字典
        """
        try:
            if not os.path.exists(self.config_file):
                # 创建默认配置文件
                default_config = {
                    "target_files": [
                        "example.txt"
                    ],
                    "rules_file": "rules.txt",
                    "backup_files": True,
                    "case_sensitive": True,
                    "output_suffix": "_replaced",
                    "output_extension": None,
                    "max_workers": 4
                }
                self.save_config(default_config)
                logger.info(f"创建默认配置文件: {self.config_file}")
                return default_config
            
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"加载配置文件: {self.config_file}")
                return config
                
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            raise
    
    def save_config(self, config: Dict):
        """
        保存配置文件
        
        Args:
            config: 配置字典
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                logger.info(f"保存配置文件: {self.config_file}")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            raise
    
    def generate_output_filename(self, input_file: str) -> str:
        """
        根据配置生成输出文件名
        
        Args:
            input_file: 输入文件路径
            
        Returns:
            输出文件路径
        """
        base_name = os.path.splitext(input_file)[0]
        original_ext = os.path.splitext(input_file)[1]
        
        suffix = self.config.get("output_suffix", "_replaced")
        new_ext = self.config.get("output_extension", original_ext)
        
        # 如果没有指定新扩展名，使用原扩展名
        if new_ext is None:
            new_ext = original_ext
        elif not new_ext.startswith('.'):
            new_ext = '.' + new_ext
        
        return f"{base_name}{suffix}{new_ext}"
    
    def parse_replacement_rules(self) -> List[Tuple[str, str]]:
        """
        从配置文件指定的规则文件中解析替换规则
        
        Returns:
            [(原字符串, 替换字符串), ...]
        """
        rules = []
        rules_file = self.config.get("rules_file", "")
        
        if not rules_file:
            logger.warning("配置文件中未指定rules_file")
            return rules
        
        if not os.path.exists(rules_file):
            logger.warning(f"规则文件不存在: {rules_file}")
            return rules
        
        try:
            with open(rules_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        # 跳过空行和注释行
                        continue
                    
                    parts = line.split()
                    if len(parts) >= 2:
                        old_str = parts[0]
                        new_str = " ".join(parts[1:])
                        rules.append((old_str, new_str))
                    else:
                        logger.warning(f"跳过无效规则 (第{line_num}行): {line}")
            
            logger.info(f"从 {rules_file} 解析到 {len(rules)} 条替换规则")
            
        except Exception as e:
            logger.error(f"读取规则文件 {rules_file} 时出错: {e}")
        
        return rules
    
    def backup_file(self, file_path: str) -> str:
        """
        备份文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            备份文件路径
        """
        backup_path = f"{file_path}.backup"
        try:
            with open(file_path, 'r', encoding='utf-8') as src, \
                 open(backup_path, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
            logger.info(f"备份文件: {file_path} -> {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"备份文件失败: {e}")
            raise
    
    def find_enum_blocks(self, content: str) -> List[Tuple[int, int]]:
        """
        找到所有枚举块的位置
        
        Args:
            content: 文件内容
            
        Returns:
            [(start_pos, end_pos), ...] 枚举块在内容中的位置
        """
        enum_blocks = []
        # 匹配 enum 关键字后跟可选的标识符，然后是花括号
        enum_pattern = r'enum\s+[^{]*\{'
        
        for match in re.finditer(enum_pattern, content, re.IGNORECASE):
            start_pos = match.start()
            # 从 enum 开始找到对应的结束花括号
            brace_count = 0
            pos = match.end() - 1  # 从第一个 { 开始
            
            while pos < len(content):
                if content[pos] == '{':
                    brace_count += 1
                elif content[pos] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = pos + 1
                        enum_blocks.append((start_pos, end_pos))
                        break
                pos += 1
        
        return enum_blocks
    
    def is_position_in_enum(self, position: int, enum_blocks: List[Tuple[int, int]]) -> bool:
        """
        检查位置是否在枚举块内
        
        Args:
            position: 要检查的位置
            enum_blocks: 枚举块列表
            
        Returns:
            是否在枚举块内
        """
        for start, end in enum_blocks:
            if start <= position < end:
                return True
        return False
    
    def safe_replace_content(self, content: str, old_str: str, new_str: str, case_sensitive: bool = True) -> Tuple[str, int]:
        """
        安全地替换内容，避免在枚举块内进行替换
        
        Args:
            content: 原始内容
            old_str: 要替换的字符串
            new_str: 替换后的字符串
            case_sensitive: 是否区分大小写
            
        Returns:
            (替换后的内容, 替换次数)
        """
        # 找到所有枚举块
        enum_blocks = self.find_enum_blocks(content)
        
        if not enum_blocks:
            # 没有枚举块，直接替换
            if case_sensitive:
                if old_str in content:
                    return content.replace(old_str, new_str), content.count(old_str)
                else:
                    return content, 0
            else:
                pattern = re.compile(re.escape(old_str), re.IGNORECASE)
                matches = pattern.findall(content)
                if matches:
                    return pattern.sub(new_str, content), len(matches)
                else:
                    return content, 0
        
        # 有枚举块，需要避免在枚举块内替换
        new_content = content
        total_replacements = 0
        
        if case_sensitive:
            # 区分大小写替换
            start_pos = 0
            while True:
                pos = new_content.find(old_str, start_pos)
                if pos == -1:
                    break
                
                # 检查是否在枚举块内
                if not self.is_position_in_enum(pos, enum_blocks):
                    # 不在枚举块内，可以替换
                    new_content = new_content[:pos] + new_str + new_content[pos + len(old_str):]
                    total_replacements += 1
                    # 调整枚举块位置（因为内容长度可能改变）
                    len_diff = len(new_str) - len(old_str)
                    if len_diff != 0:
                        enum_blocks = [(start + (len_diff if start > pos else 0), 
                                       end + (len_diff if end > pos else 0)) 
                                      for start, end in enum_blocks]
                    start_pos = pos + len(new_str)
                else:
                    # 在枚举块内，跳过
                    start_pos = pos + len(old_str)
        else:
            # 不区分大小写替换
            pattern = re.compile(re.escape(old_str), re.IGNORECASE)
            start_pos = 0
            
            while True:
                match = pattern.search(new_content, start_pos)
                if not match:
                    break
                
                pos = match.start()
                
                # 检查是否在枚举块内
                if not self.is_position_in_enum(pos, enum_blocks):
                    # 不在枚举块内，可以替换
                    new_content = new_content[:pos] + new_str + new_content[match.end():]
                    total_replacements += 1
                    # 调整枚举块位置
                    len_diff = len(new_str) - len(match.group())
                    if len_diff != 0:
                        enum_blocks = [(start + (len_diff if start > pos else 0), 
                                       end + (len_diff if end > pos else 0)) 
                                      for start, end in enum_blocks]
                    start_pos = pos + len(new_str)
                else:
                    # 在枚举块内，跳过
                    start_pos = match.end()
        
        return new_content, total_replacements
    
    def replace_in_file(self, file_path: str, rules: List[Tuple[str, str]]) -> Dict:
        """
        在文件中执行替换并生成新文件
        
        Args:
            file_path: 文件路径
            rules: 替换规则列表
            
        Returns:
            替换统计信息
        """
        if not os.path.exists(file_path):
            logger.warning(f"文件不存在: {file_path}")
            return {"success": False, "error": "文件不存在"}
        
        try:
            # 生成输出文件名
            output_file = self.generate_output_filename(file_path)
            
            # 备份原文件（如果需要）
            if self.config.get("backup_files", True):
                self.backup_file(file_path)
            
            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            total_replacement_count = 0
            replaced_rules = []
            
            # 执行替换
            for old_str, new_str in rules:
                content, count = self.safe_replace_content(
                    content, old_str, new_str, 
                    self.config.get("case_sensitive", True)
                )
                
                if count > 0:
                    total_replacement_count += count
                    replaced_rules.append((old_str, new_str, count))
                    with self.lock:
                        logger.info(f"[{threading.current_thread().name}] 替换 '{old_str}' -> '{new_str}' ({count} 次)")
                else:
                    with self.lock:
                        logger.debug(f"[{threading.current_thread().name}] 未找到字段: {old_str}")
            
            # 写入新文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            with self.lock:
                if total_replacement_count > 0:
                    logger.info(f"[{threading.current_thread().name}] 处理完成：{file_path} -> {output_file}，共替换 {total_replacement_count} 处")
                else:
                    logger.info(f"[{threading.current_thread().name}] 处理完成：{file_path} -> {output_file}，未发现需要替换的内容")
            
            return {
                "success": True,
                "input_file": file_path,
                "output_file": output_file,
                "total_replacements": total_replacement_count,
                "replaced_rules": replaced_rules
            }
            
        except Exception as e:
            with self.lock:
                logger.error(f"[{threading.current_thread().name}] 处理文件 {file_path} 时出错: {e}")
            return {"success": False, "error": str(e)}
    
    def process_file_wrapper(self, file_path: str, rules: List[Tuple[str, str]]) -> Dict:
        """
        文件处理包装器，用于多线程处理
        
        Args:
            file_path: 文件路径
            rules: 替换规则列表
            
        Returns:
            处理结果
        """
        with self.lock:
            logger.info(f"[{threading.current_thread().name}] 开始处理文件: {file_path}")
        
        result = self.replace_in_file(file_path, rules)
        
        with self.lock:
            logger.info(f"[{threading.current_thread().name}] 完成处理文件: {file_path}")
        
        return result
    
    def run(self, target_files: List[str] = None) -> Dict:
        """
        运行替换任务
        
        Args:
            target_files: 目标文件列表，如果为None则使用配置文件中的设置
            
        Returns:
            执行结果统计
        """
        if target_files is None:
            target_files = self.config.get("target_files", [])
        
        rules = self.parse_replacement_rules()
        if not rules:
            logger.warning("没有找到替换规则")
            return {"success": False, "error": "没有替换规则"}
        
        # 获取最大工作线程数
        max_workers = self.config.get("max_workers", 4)
        logger.info(f"使用 {max_workers} 个线程处理 {len(target_files)} 个文件")
        
        results = []
        total_replacements = 0
        
        # 使用线程池处理文件
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_file = {
                executor.submit(self.process_file_wrapper, file_path, rules): file_path 
                for file_path in target_files
            }
            
            # 收集结果
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    if result.get("success"):
                        total_replacements += result.get("total_replacements", 0)
                except Exception as e:
                    with self.lock:
                        logger.error(f"处理文件 {file_path} 时发生异常: {e}")
                    results.append({"success": False, "error": str(e), "input_file": file_path})
        
        logger.info(f"替换任务完成，总计替换 {total_replacements} 处")
        
        return {
            "success": True,
            "total_files": len(target_files),
            "total_replacements": total_replacements,
            "results": results
        }
    
    def add_replacement_rule(self, old_str: str, new_str: str):
        """
        添加替换规则到规则文件
        
        Args:
            old_str: 要替换的字符串
            new_str: 替换后的字符串
        """
        rules_file = self.config.get("rules_file", "rules.txt")
        rule_line = f"{old_str} {new_str}\n"
        
        try:
            # 检查规则是否已存在
            existing_rules = []
            if os.path.exists(rules_file):
                with open(rules_file, 'r', encoding='utf-8') as f:
                    existing_rules = f.readlines()
            
            # 检查是否已存在相同的规则
            for line in existing_rules:
                if line.strip() and line.strip().split()[0] == old_str:
                    logger.info(f"规则已存在: {old_str} -> {new_str}")
                    return
            
            # 添加新规则
            with open(rules_file, 'a', encoding='utf-8') as f:
                f.write(rule_line)
            
            logger.info(f"添加替换规则到 {rules_file}: {old_str} -> {new_str}")
            
        except Exception as e:
            logger.error(f"添加规则失败: {e}")
    
    def remove_replacement_rule(self, old_str: str):
        """
        从规则文件中移除替换规则
        
        Args:
            old_str: 要移除的原字符串
        """
        rules_file = self.config.get("rules_file", "rules.txt")
        
        if not os.path.exists(rules_file):
            logger.warning(f"规则文件不存在: {rules_file}")
            return
        
        try:
            with open(rules_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            updated_lines = []
            removed = False
            
            for line in lines:
                if line.strip() and not line.strip().startswith('#'):
                    if line.strip().split()[0] == old_str:
                        removed = True
                        logger.info(f"移除规则: {line.strip()}")
                        continue
                updated_lines.append(line)
            
            if removed:
                with open(rules_file, 'w', encoding='utf-8') as f:
                    f.writelines(updated_lines)
                logger.info(f"规则文件已更新: {rules_file}")
            else:
                logger.info(f"未找到规则: {old_str}")
                
        except Exception as e:
            logger.error(f"移除规则失败: {e}")
    
    def create_sample_rules_file(self):
        """
        创建示例规则文件
        """
        rules_file = self.config.get("rules_file", "rules.txt")
        
        if os.path.exists(rules_file):
            logger.info(f"规则文件已存在: {rules_file}")
            return
        
        sample_rules = [
            "# 这是规则文件示例",
            "# 格式: 原字符串 替换字符串",
            "# 以#开头的行是注释",
            "# 注意: 在enum花括号内的内容不会被替换",
            "",
            "FAABCMJGOEO GetPlayerTokenRsp",
            "AELCGAFNHGN GetPlayerTokenReq", 
            "FIDPEPOEIDD PlayerLoginReq",
            "EJMEHGBCHOG account_uid",
            "OHKGLEKNNBJ token",
            "FBGOPGBEJFC country_code",
            "EJNFKLDKGGH SocialDetail"
        ]
        
        try:
            with open(rules_file, 'w', encoding='utf-8') as f:
                for rule in sample_rules:
                    f.write(rule + '\n')
            logger.info(f"创建示例规则文件: {rules_file}")
        except Exception as e:
            logger.error(f"创建规则文件失败: {e}")

def main():
    """
    主函数，支持命令行参数
    """
    parser = argparse.ArgumentParser(description="文件内容替换工具")
    parser.add_argument("--config", "-c", default="config.json", help="配置文件路径")
    parser.add_argument("--files", "-f", nargs="*", help="目标文件列表")
    parser.add_argument("--add-rule", "-a", nargs=2, metavar=("OLD", "NEW"), help="添加替换规则")
    parser.add_argument("--remove-rule", "-r", help="移除替换规则")
    parser.add_argument("--list-rules", "-l", action="store_true", help="列出所有规则")
    parser.add_argument("--max-workers", "-w", type=int, help="最大工作线程数")
    parser.add_argument("--create-sample", "-s", action="store_true", help="创建示例规则文件")
    
    args = parser.parse_args()
    
    try:
        replacer = FileReplacer(args.config)
        
        # 如果指定了最大工作线程数，更新配置
        if args.max_workers:
            replacer.config["max_workers"] = args.max_workers
        
        if args.add_rule:
            replacer.add_replacement_rule(args.add_rule[0], args.add_rule[1])
        elif args.remove_rule:
            replacer.remove_replacement_rule(args.remove_rule)
        elif args.list_rules:
            rules = replacer.parse_replacement_rules()
            print("当前替换规则:")
            for i, (old, new) in enumerate(rules, 1):
                print(f"{i:2d}. {old} -> {new}")
        elif args.create_sample:
            replacer.create_sample_rules_file()
        else:
            result = replacer.run(args.files)
            if result["success"]:
                print(f"替换完成！共处理 {result['total_files']} 个文件，替换 {result['total_replacements']} 处")
                print("生成的文件:")
                for res in result["results"]:
                    if res.get("success"):
                        print(f"  {res['input_file']} -> {res['output_file']}")
            else:
                print(f"替换失败: {result.get('error', '未知错误')}")
                
    except Exception as e:
        logger.error(f"程序执行出错: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())