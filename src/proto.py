#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件内容替换脚本 - 多进程超高性能版
根据配置文件中的规则替换指定文件中的内容
优化：多进程并行处理、充分利用多核CPU、高效算法
新增：proto字段类型修复、枚举内部替换开关、消息名前缀并行清理
"""

import json
import os
import re
from typing import Dict, List, Tuple, Set
import argparse
import logging
from multiprocessing import Pool, cpu_count
from functools import partial
import time

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FileReplacer:
    def __init__(self, config_file: str = "config.json"):
        """初始化文件替换器"""
        self.config_file = config_file
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """加载配置文件"""
        try:
            if not os.path.exists(self.config_file):
                default_config = {
                    "target_files": ["example.txt"],
                    "rules_file": "rules.txt",
                    "backup_files": True,
                    "case_sensitive": True,
                    "output_suffix": "_replaced",
                    "output_extension": None,
                    "max_workers": cpu_count(),  # 默认使用所有CPU核心
                    "fix_proto_field_type": True,
                    "replace_in_enum": False
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
        """保存配置文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            raise
    
    def parse_replacement_rules(self) -> List[Tuple[str, str]]:
        """解析替换规则"""
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
    
    def run(self, target_files: List[str] = None) -> Dict:
        """运行替换任务（多进程版）"""
        if target_files is None:
            target_files = self.config.get("target_files", [])
        
        rules = self.parse_replacement_rules()
        if not rules:
            logger.warning("没有找到替换规则")
            return {"success": False, "error": "没有替换规则"}
        
        max_workers = self.config.get("max_workers", cpu_count())
        replace_in_enum = self.config.get("replace_in_enum", False)
        fix_proto = self.config.get("fix_proto_field_type", True)
        
        logger.info(f"==========================================")
        logger.info(f"使用 {max_workers} 个进程并行处理 {len(target_files)} 个文件")
        logger.info(f"CPU核心数: {cpu_count()}")
        logger.info(f"共有 {len(rules)} 条替换规则")
        logger.info(f"枚举内部替换: {'启用' if replace_in_enum else '禁用'}")
        logger.info(f"Proto前缀清理: {'启用' if fix_proto else '禁用'}")
        logger.info(f"==========================================")
        
        start_time = time.time()
        
        # 使用多进程池
        with Pool(processes=max_workers) as pool:
            # 创建处理函数（带配置参数）
            process_func = partial(
                process_single_file_worker,
                rules=rules,
                config=self.config
            )
            
            # 并行处理所有文件
            results = pool.map(process_func, target_files)
        
        # 统计结果
        successful_files = sum(1 for r in results if r.get("success"))
        failed_files = len(results) - successful_files
        total_replacements = sum(r.get("total_replacements", 0) for r in results if r.get("success"))
        total_prefix_cleaned = sum(r.get("prefix_cleaned", 0) for r in results if r.get("success"))
        
        elapsed_time = time.time() - start_time
        
        # 输出结果
        for result in results:
            if result.get("success"):
                log_msg = f"✓ {result['input_file']} -> {result['output_file']}"
                if result.get("prefix_cleaned", 0) > 0:
                    log_msg += f" (前缀: {result['prefix_cleaned']})"
                log_msg += f" (替换: {result['total_replacements']})"
                logger.info(log_msg)
            else:
                logger.error(f"✗ {result.get('file', 'unknown')}: {result.get('error', '未知错误')}")
        
        logger.info(f"\n==========================================")
        logger.info(f"替换任务完成！")
        logger.info(f"  处理文件: {len(target_files)} 个")
        logger.info(f"  成功: {successful_files} 个")
        logger.info(f"  失败: {failed_files} 个")
        if total_prefix_cleaned > 0:
            logger.info(f"  前缀清理: {total_prefix_cleaned} 处")
        logger.info(f"  总计替换: {total_replacements} 处")
        logger.info(f"  总耗时: {elapsed_time:.2f} 秒")
        logger.info(f"  平均速度: {len(target_files)/elapsed_time:.2f} 文件/秒")
        logger.info(f"==========================================")
        
        return {
            "success": True,
            "total_files": len(target_files),
            "successful_files": successful_files,
            "failed_files": failed_files,
            "total_prefix_cleaned": total_prefix_cleaned,
            "total_replacements": total_replacements,
            "elapsed_time": elapsed_time,
            "results": results
        }
    
    def add_replacement_rule(self, old_str: str, new_str: str):
        """添加替换规则到规则文件"""
        rules_file = self.config.get("rules_file", "rules.txt")
        rule_line = f"{old_str} {new_str}\n"
        
        try:
            existing_rules = []
            if os.path.exists(rules_file):
                with open(rules_file, 'r', encoding='utf-8') as f:
                    existing_rules = f.readlines()
            
            for line in existing_rules:
                if line.strip() and line.strip().split()[0] == old_str:
                    logger.info(f"规则已存在: {old_str} -> {new_str}")
                    return
            
            with open(rules_file, 'a', encoding='utf-8') as f:
                f.write(rule_line)
            
            logger.info(f"添加替换规则到 {rules_file}: {old_str} -> {new_str}")
            
        except Exception as e:
            logger.error(f"添加规则失败: {e}")
    
    def remove_replacement_rule(self, old_str: str):
        """从规则文件中移除替换规则"""
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
        """创建示例规则文件"""
        rules_file = self.config.get("rules_file", "rules.txt")
        
        if os.path.exists(rules_file):
            logger.info(f"规则文件已存在: {rules_file}")
            return
        
        sample_rules = [
            "# 这是规则文件示例",
            "# 格式: 原字符串 替换字符串",
            "# 以#开头的行是注释",
            "# 注意: 默认情况下enum花括号内的内容不会被替换",
            "# 可在config.json中设置 replace_in_enum: true 来启用枚举内部替换",
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


# ============ 工作进程函数（必须在模块级别定义） ============

def generate_output_filename(input_file: str, config: Dict) -> str:
    """生成输出文件名"""
    base_name = os.path.splitext(input_file)[0]
    original_ext = os.path.splitext(input_file)[1]
    
    suffix = config.get("output_suffix", "_replaced")
    new_ext = config.get("output_extension", original_ext)
    
    if new_ext is None:
        new_ext = original_ext
    elif not new_ext.startswith('.'):
        new_ext = '.' + new_ext
    
    return f"{base_name}{suffix}{new_ext}"


def backup_file(file_path: str) -> str:
    """备份文件"""
    backup_path = f"{file_path}.backup"
    try:
        with open(file_path, 'r', encoding='utf-8') as src, \
             open(backup_path, 'w', encoding='utf-8') as dst:
            dst.write(src.read())
        return backup_path
    except Exception as e:
        raise Exception(f"备份文件失败: {e}")


def extract_message_names(content: str) -> Set[str]:
    """
    提取所有 message 和 enum 的名称
    优化：只提取非混淆的名称（至少包含一个小写字母或下划线）
    跳过全大写的混淆名称，大幅提升性能
    """
    names = set()
    
    # 匹配 message 定义
    message_pattern = r'\bmessage\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{'
    for match in re.finditer(message_pattern, content):
        name = match.group(1)
        # 跳过混淆名称（全大写字母，无小写和下划线）
        # 例如跳过：FAABCMJGOEO，保留：ToTheMoonObstacleInfo
        if not name.isupper() or '_' in name:
            names.add(name)
    
    # 匹配 enum 定义
    enum_pattern = r'\benum\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{'
    for match in re.finditer(enum_pattern, content):
        name = match.group(1)
        # 同样跳过混淆名称
        if not name.isupper() or '_' in name:
            names.add(name)
    
    return names


def clean_message_prefixes(content: str, config: Dict) -> Tuple[str, int]:
    """
    清理消息名前缀（超高速优化版）
    1. 只提取非混淆的消息名（跳过全大写的混淆名）
    2. 使用编译好的正则表达式批量处理
    3. 一次性替换所有匹配，避免重复遍历
    
    例如: ToTheMoonQueryPathReq.FilterType -> FilterType
    
    Returns:
        (清理后的内容, 清理次数)
    """
    if not config.get("fix_proto_field_type", True):
        return content, 0
    
    message_names = extract_message_names(content)
    
    if not message_names:
        return content, 0
    
    # 构建一个大的正则表达式，一次性匹配所有消息名前缀
    # 使用 (?:name1|name2|name3) 的方式
    escaped_names = [re.escape(name) for name in message_names]
    combined_pattern = r'(?<![A-Za-z0-9_])(?:' + '|'.join(escaped_names) + r')\.([A-Za-z_][A-Za-z0-9_]*)(?=\s|=|;|,|\)|\]|})'
    
    # 编译正则表达式（提升性能）
    pattern = re.compile(combined_pattern)
    
    # 找到所有匹配
    matches = []
    for match in pattern.finditer(content):
        # 只保留类型名（group(1)是点后面的部分）
        type_name = match.group(1)
        matches.append((match.start(), match.end(), type_name))
    
    if not matches:
        return content, 0
    
    # 从后往前替换（避免位置偏移）
    matches.reverse()
    
    result = content
    for start, end, type_name in matches:
        result = result[:start] + type_name + result[end:]
    
    return result, len(matches)


def find_enum_blocks(content: str) -> Set[range]:
    """找到所有枚举块的位置"""
    enum_blocks = set()
    enum_pattern = r'enum\s+[^{]*\{'
    
    for match in re.finditer(enum_pattern, content, re.IGNORECASE):
        start_pos = match.start()
        brace_count = 0
        pos = match.end() - 1
        
        while pos < len(content):
            if content[pos] == '{':
                brace_count += 1
            elif content[pos] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = pos + 1
                    enum_blocks.add(range(start_pos, end_pos))
                    break
            pos += 1
    
    return enum_blocks


def is_in_enum(pos: int, enum_blocks: Set[range]) -> bool:
    """检查位置是否在枚举块内"""
    for block_range in enum_blocks:
        if pos in block_range:
            return True
    return False


def fast_replace_content(content: str, rules: List[Tuple[str, str]], config: Dict) -> Tuple[str, Dict]:
    """
    超高性能替换算法（优化版）
    1. 预先过滤掉内容中不存在的规则
    2. 使用正则表达式编译缓存
    3. 批量处理替换，减少字符串操作次数
    
    Returns:
        (替换后的内容, {规则: 替换次数})
    """
    replace_in_enum = config.get("replace_in_enum", False)
    case_sensitive = config.get("case_sensitive", True)
    
    # 如果不替换枚举内部，则找到所有枚举块
    enum_blocks = set()
    if not replace_in_enum:
        enum_blocks = find_enum_blocks(content)
    
    result = content
    final_counts = {}
    
    # 预先过滤规则：只保留在内容中存在的规则
    active_rules = []
    for old_str, new_str in rules:
        if case_sensitive:
            if old_str in result:
                active_rules.append((old_str, new_str))
        else:
            if old_str.lower() in result.lower():
                active_rules.append((old_str, new_str))
    
    if not active_rules:
        return result, final_counts
    
    # 逐个规则处理（已经过滤）
    for old_str, new_str in active_rules:
        # 找到所有匹配位置
        matches = []
        if case_sensitive:
            # 使用更快的字符串查找
            pos = 0
            old_len = len(old_str)
            while True:
                idx = result.find(old_str, pos)
                if idx == -1:
                    break
                matches.append((idx, idx + old_len))
                pos = idx + 1
        else:
            pattern = re.compile(re.escape(old_str), re.IGNORECASE)
            matches = [(m.start(), m.end()) for m in pattern.finditer(result)]
        
        if not matches:
            continue
        
        # 根据配置过滤匹配
        if not replace_in_enum and enum_blocks:
            matches = [(s, e) for s, e in matches if not is_in_enum(s, enum_blocks)]
        
        if not matches:
            continue
        
        # 使用列表构建代替字符串拼接（更快）
        matches.reverse()
        for start, end in matches:
            result = result[:start] + new_str + result[end:]
        
        count = len(matches)
        final_counts[f"{old_str} -> {new_str}"] = count
        
        # 更新枚举块位置
        if not replace_in_enum and enum_blocks and count > 0:
            len_diff = len(new_str) - len(old_str)
            if len_diff != 0:
                new_enum_blocks = set()
                for block_range in enum_blocks:
                    shifts = sum(1 for s, e in matches if s < block_range.start)
                    shift = shifts * len_diff
                    new_enum_blocks.add(range(
                        block_range.start + shift,
                        block_range.stop + shift
                    ))
                enum_blocks = new_enum_blocks
    
    return result, final_counts


def process_single_file_worker(file_path: str, rules: List[Tuple[str, str]], config: Dict) -> Dict:
    """
    工作进程函数：处理单个文件
    必须在模块级别定义以支持多进程
    """
    try:
        if not os.path.exists(file_path):
            return {"success": False, "error": "文件不存在", "file": file_path}
        
        # 生成输出文件名
        output_file = generate_output_filename(file_path, config)
        
        # 备份原文件（如果需要）
        if config.get("backup_files", True):
            backup_file(file_path)
        
        # 读取文件内容
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 执行规则替换
        new_content, rule_counts = fast_replace_content(content, rules, config)
        
        # Proto文件后处理：清理消息名前缀
        prefix_cleaned = 0
        if file_path.endswith('.proto'):
            new_content, prefix_cleaned = clean_message_prefixes(new_content, config)
        
        # 写入新文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        total_replacements = sum(rule_counts.values())
        
        return {
            "success": True,
            "input_file": file_path,
            "output_file": output_file,
            "prefix_cleaned": prefix_cleaned,
            "total_replacements": total_replacements,
            "rule_counts": rule_counts
        }
        
    except Exception as e:
        return {
            "success": False, 
            "error": str(e), 
            "file": file_path
        }


def main():
    """主函数，支持命令行参数"""
    parser = argparse.ArgumentParser(description="文件内容替换工具 - 多进程超高性能版")
    parser.add_argument("--config", "-c", default="config.json", help="配置文件路径")
    parser.add_argument("--files", "-f", nargs="*", help="目标文件列表")
    parser.add_argument("--add-rule", "-a", nargs=2, metavar=("OLD", "NEW"), help="添加替换规则")
    parser.add_argument("--remove-rule", "-r", help="移除替换规则")
    parser.add_argument("--list-rules", "-l", action="store_true", help="列出所有规则")
    parser.add_argument("--max-workers", "-w", type=int, help="最大工作进程数")
    parser.add_argument("--create-sample", "-s", action="store_true", help="创建示例规则文件")
    parser.add_argument("--replace-in-enum", action="store_true", help="启用枚举内部替换")
    parser.add_argument("--no-replace-in-enum", action="store_true", help="禁用枚举内部替换")
    parser.add_argument("--fix-proto", action="store_true", help="启用proto前缀清理")
    parser.add_argument("--no-fix-proto", action="store_true", help="禁用proto前缀清理")
    
    args = parser.parse_args()
    
    try:
        replacer = FileReplacer(args.config)
        
        if args.max_workers:
            replacer.config["max_workers"] = args.max_workers
        
        if args.replace_in_enum:
            replacer.config["replace_in_enum"] = True
        elif args.no_replace_in_enum:
            replacer.config["replace_in_enum"] = False
        
        if args.fix_proto:
            replacer.config["fix_proto_field_type"] = True
        elif args.no_fix_proto:
            replacer.config["fix_proto_field_type"] = False
        
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
                print(f"\n✅ 所有任务完成！")
                print(f"   处理速度: {result['total_files']/result['elapsed_time']:.2f} 文件/秒")
            else:
                print(f"❌ 替换失败: {result.get('error', '未知错误')}")
                
    except Exception as e:
        logger.error(f"程序执行出错: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())