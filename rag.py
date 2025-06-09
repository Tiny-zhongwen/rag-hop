import torch
import faiss
import numpy as np
from llama_index.core.node_parser import SentenceSplitter
import re  # 正则表达式检查文件的编码，避免生成乱码
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import shutil
from typing import Optional
from openai import OpenAI
import gradio as gr
import os
import fitz  # PyMuPDF
import chardet  # 用于自动检测编码
import traceback
from config import Config  # 导入配置文件

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# 创建知识库根目录和临时文件目录
KB_BASE_DIR = Config.kb_base_dir
os.makedirs(KB_BASE_DIR, exist_ok=True)

# 创建默认知识库目录
DEFAULT_KB = Config.default_kb
DEFAULT_KB_DIR = os.path.join(KB_BASE_DIR, DEFAULT_KB)
os.makedirs(DEFAULT_KB_DIR, exist_ok=True)

# 创建临时输出目录
OUTPUT_DIR = Config.output_dir
os.makedirs(OUTPUT_DIR, exist_ok=True)

client = OpenAI(
    api_key=Config.llm_api_key,
    base_url=Config.llm_base_url
)


class DeepSeekClient:
    def generate_answer(self, system_prompt, user_prompt, model=Config.llm_model):
        response = client.chat.completions.create(
            model=Config.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            stream=False
        )
        return response.choices[0].message.content.strip()


# 获取知识库列表
def get_knowledge_bases() -> List[str]:
    """获取所有知识库名称"""
    try:
        # 如果知识库目录不存在，则创建
        if not os.path.exists(KB_BASE_DIR):
            os.makedirs(KB_BASE_DIR, exist_ok=True)

        # 获取知识库目录下的所有子目录
        kb_dirs = [d for d in os.listdir(KB_BASE_DIR)
                   if os.path.isdir(os.path.join(KB_BASE_DIR, d))]

        # 确保默认知识库存在
        if DEFAULT_KB not in kb_dirs:
            os.makedirs(os.path.join(KB_BASE_DIR, DEFAULT_KB), exist_ok=True)
            kb_dirs.append(DEFAULT_KB)

        # 返回所有知识库名称，按字母顺序排序
        return sorted(kb_dirs)
    except Exception as e:
        # 如果获取知识库列表失败，则打印错误信息，并返回默认知识库名称
        print(f"获取知识库列表失败: {str(e)}")
        return [DEFAULT_KB]


# 创建新知识库
def create_knowledge_base(kb_name: str) -> str:
    """创建新的知识库"""
    try:
        if not kb_name or not kb_name.strip():
            return "错误：知识库名称不能为空"

        # 净化知识库名称，只允许字母、数字、下划线和中文
        kb_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', kb_name.strip())

        kb_path = os.path.join(KB_BASE_DIR, kb_name)
        if os.path.exists(kb_path):
            return f"知识库 '{kb_name}' 已存在"

        os.makedirs(kb_path, exist_ok=True)
        return f"知识库 '{kb_name}' 创建成功"
    except Exception as e:
        return f"创建知识库失败: {str(e)}"


# 删除知识库
def delete_knowledge_base(kb_name: str) -> str:
    """删除指定的知识库"""
    try:
        if kb_name == DEFAULT_KB:
            return f"无法删除默认知识库 '{DEFAULT_KB}'"

        kb_path = os.path.join(KB_BASE_DIR, kb_name)
        if not os.path.exists(kb_path):
            return f"知识库 '{kb_name}' 不存在"

        shutil.rmtree(kb_path)  # 删除目录及其内容
        return f"知识库 '{kb_name}' 已删除"
    except Exception as e:
        return f"删除知识库失败: {str(e)}"


# 获取知识库文件列表
def get_kb_files(kb_name: str) -> List[str]:
    """获取指定知识库中的文件列表"""
    try:
        kb_path = os.path.join(KB_BASE_DIR, kb_name)
        if not os.path.exists(kb_path):
            return []

        # 获取所有文件（排除索引文件和元数据文件）
        files = [f for f in os.listdir(kb_path)
                 if os.path.isfile(os.path.join(kb_path, f)) and
                 not f.endswith(('.index', '.json'))]

        return sorted(files)
    except Exception as e:
        print(f"获取知识库文件列表失败: {str(e)}")
        return []


# 1.5 语义分块函数
def semantic_chunk(text: str, chunk_size=800, chunk_overlap=20) -> List[dict]:
    # 定义一个增强的句子分割器类
    class EnhancedSentenceSplitter(SentenceSplitter):  # 继承自Sent enceSplitter类
        """增强的句子分割器"""

        def __init__(self, *args, **kwargs):
            # 自定义分隔符
            custom_seps = ["；", "!", "?", "\n"]
            # 将分隔符添加到参数中
            separators = [kwargs.get("separator", "。")] + custom_seps  # ['。', '；', '!', '?', '\n']
            print(f"自定义分隔符: {separators}-------------")
            kwargs["separator"] = '|'.join(map(re.escape, separators))  # 。|；|!|\?|\
            print(f"拼接后的字符串：{kwargs['separator']}=============")
            super().__init__(*args, **kwargs)

        # 重写分割文本的方法
        def _split_text(self, text: str, **kwargs) -> List[str]:
            # 使用正则表达式分割文本
            splits = re.split(f'({self.separator})', text)
            print(f"分割后的文本: {splits}-----------")
            chunks = []  # 存储分割后的文本块
            current_chunk = []  # 存储当前正在处理的文本块
            for part in splits:
                part = part.strip()
                if not part:
                    continue
                if re.fullmatch(self.separator, part):
                    if current_chunk:
                        chunks.append("".join(current_chunk))
                        current_chunk = []
                else:
                    current_chunk.append(part)
            if current_chunk:
                chunks.append("".join(current_chunk))
            return [chunk.strip() for chunk in chunks if chunk.strip()]

    # 创建一个增强的句子分割器实例
    text_splitter = EnhancedSentenceSplitter(
        separator="。",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        paragraph_separator="\n\n"  # 文本将根据两个连续的换行符进行段落分割
    )

    # 初始化段落列表和当前段落
    paragraphs = []
    current_para = []
    current_len = 0

    # 遍历文本中的段落
    for para in text.split("\n\n"):  # 将文本按段落分割 \n\n表示段落
        para = para.strip()
        para_len = len(para)
        if para_len == 0:
            continue
        # 如果当前段落长度加上新段落长度小于等于chunk_size，则将新段落添加到当前段落中
        if current_len + para_len <= chunk_size:
            current_para.append(para)
            current_len += para_len
        else:
            # 否则，将当前段落添加到段落列表中，并重新初始化当前段落
            if current_para:
                paragraphs.append("\n".join(current_para))
            current_para = [para]
            current_len = para_len

    # 如果当前段落不为空，则将其添加到段落列表中
    if current_para:
        paragraphs.append("\n".join(current_para))

    # 初始化chunk_data_list和chunk_id
    chunk_data_list = []
    chunk_id = 0
    # 遍历段落列表
    for para in paragraphs:
        # 使用增强的句子分割器分割段落
        chunks = text_splitter.split_text(para)
        # 遍历分割后的段落
        for chunk in chunks:
            # 如果段落长度小于20，则跳过
            if len(chunk) < 20:
                continue
            # 将段落添加到chunk_data_list中
            chunk_data_list.append({
                "id": f'chunk{chunk_id}',
                "chunk": chunk,
                "method": "semantic_chunk"
            })
            chunk_id += 1
    # 返回chunk_data_list
    return chunk_data_list


"""
build_faiss_index 函数中动态选择Faiss索引类型的逻辑非常关键。
IndexFlatIP 对于小数
据集来说简单有效，它会计算查询向量与索引中所有向量的相似度。但当向量数量巨大时，
这种暴力搜索会非常慢。
IndexIVFFlat 通过将向量空间划分为 nlist 个区域（Voronoi单元），
在搜索时仅在查询向量最可能落入的少数几个区域内进行搜索，从而大大提高搜索效率，
尽管这可能带来微小的精度损失（因为最近邻可能恰好在未被搜索的区域的边界上）。训练
步骤 (index.train) 就是用来确定这些区域的划分。这种根据数据规模自动调整策略的做法，
体现了对库特性和性能优化的深入理解。
"""


# 构建Faiss索引
def build_faiss_index(vector_file, index_path, metadata_path):
    """
    从包含文本块及其向量的JSON文件 (vector_file) 构建Faiss索引，并将索引保
    存到 index_path，相关的元数据保存到 metadata_path。
    :param vector_file: 包含文本块及其向量的JSON文件路径
    :param index_path: 保存Faiss索引的文件路径
    :param metadata_path: 保存元数据的文件路径,向量化检索最终用到的数据
    """

    try:
        with open(vector_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not data:
            raise ValueError("向量数据为空，请检查输入文件。")

        # 确认所有数据项都有向量
        valid_data = []
        for item in data:
            if 'vector' in item and item['vector']:  # 检查当前数据项item是否包含'vector'并且该键对应的值不为空
                valid_data.append(item)
            else:
                print(f"警告: 跳过没有向量的数据项 ID: {item.get('id', '未知')}")

        if not valid_data:
            raise ValueError("没有找到任何有效的向量数据。")

        # 提取向量
        """
        for item in valid_data 部分是一个循环，它会依次处理 valid_data 列表中的每一个元素。
        item['vector'] 是在每次循环中执行的代码，用于从当前字典item中提取出 'vector' 键对应的值
        """
        vectors = [item['vector'] for item in valid_data]
        vectors = np.array(vectors, dtype=np.float32)

        if vectors.size == 0:
            raise ValueError("向量数组为空，转换失败。")

        # 检查向量维度
        dim = vectors.shape[1]  # shape[1] 表示矩阵的列数，即每个向量的维度（特征数）
        n_vectors = vectors.shape[0]  # shape[0] 表示矩阵的行数，即向量的数量
        print(f"构建索引: {n_vectors} 个向量，每个向量维度: {dim}")

        # 确定索引类型和参数
        """
        计算一个推荐的 nlist 值（IndexIVFFlat 的聚类中心数）,这是一种
        启发式规则，用于平衡索引大小、搜索速度和精度
        """
        max_nlist = n_vectors // 39  # n_vectors // 39 是计算 nlist 的最大值，其中 39 是一个经验值，表示每个 nlist 中应该包含的向量数量
        nlist = min(max_nlist,
                    128) if max_nlist >= 1 else 1  # nlist 是用于构建索引的参数，它表示索引中的子空间数量。这里我们选择 nlist 的最大值和 128 中的较小者作为最终值

        """
        如果向量数量足够大,则选择`IndexIVFFlat`索引，否则使用`IndexFlatIP` 是索引
        IndexIVFFlat 是一种基于聚类的倒排文件索引，可以提供更快的搜索速度和更高的精度，但需要更多的内存和计算资源,适用于大规模数据集
        IndexFlatIP: 是一种基于线性搜索的索引，适用于小规模数据集，搜索速度较快，但精度较低
        """
        if nlist >= 1 and n_vectors >= nlist * 39:
            print(f"使用 IndexIVFFlat 索引，nlist={nlist}")
            quantizer = faiss.IndexFlatIP(dim)
            index = faiss.IndexIVFFlat(quantizer, dim, nlist)
            if not index.is_trained:
                index.train(vectors)  # 需要训练步骤来确定聚类中心
            index.add(vectors)
        else:
            print(f"使用 IndexFlatIP 索引")
            index = faiss.IndexFlatIP(dim)
            index.add(vectors)

        faiss.write_index(index, index_path)
        print(f"成功写入索引到 {index_path}")

        # 创建元数据 写入json文件中
        metadata = [{'id': item['id'], 'chunk': item['chunk'], 'method': item['method']} for item in valid_data]
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)
        print(f"成功写入元数据到 {metadata_path}")

        return True
    except Exception as e:
        print(f"构建索引失败: {str(e)}")
        traceback.print_exc()
        raise


# 1.6 向量化文件内容
def vectorize_file(data_list, output_file_path, field_name="chunk"):
    """
    向量化文件内容，处理长度限制并确保输入有效,并将包含向量的结果保存到JSON文件中

    process_and_index_files 函数是知识库构建的核心驱动者。它将之前定义的各个单一功能（文件
    读取、清理、分块、向量化、索引）串联成一个完整的自动化流程。使用 ThreadPoolExecutor 并行
    处理文件是一个重要的优化，特别是当用户一次上传多个文件时，可以显著减少总处理时间，因
    为文件读取和初步的文本提取通常是I/O密集型或CPU密集型（对于PDF解析）的操作，可以从并
    行化中受益。整个流程中对中间产物（如分块JSON、向量JSON）的保存，以及对每一步结果的校
    验，都体现了构建鲁棒数据处理管道的良好实践

    data_list: 数据列表，每个元素是一个字典，包含需要向量的字段
    output_file_path: 输出文件路径,用于保存向量化后的数据
    field_name: 需要向量的字段名称
    """

    # 检查输入数据
    if not data_list:
        print("警告: 没有数据需要向量化")
        with open(output_file_path, 'w', encoding='utf-8') as outfile:  # w表示写入模式
            """
            这行代码将一个空列表（[]）以JSON格式写入前面打开的文件。
            ensure_ascii=False参数表示在输出的JSON字符串中，非ASCII字符将被输出为它们实际的字符而不是转义序列。
            indent=4参数表示在输出的JSON字符串中，每一级缩进将使用4个空格，使输出的JSON字符串更易读。
            """
            json.dump([], outfile, ensure_ascii=False, indent=4)
        return

    # 预处理文本
    # 准备查询文本，确保每个文本有效且长度适中
    valid_data = []
    valid_texts = []

    for data in data_list:
        text = data.get(field_name, "")  # 获取指定字段的内容,如果字段不存在则返回空字符串
        # 确保文本不为空且长度合适
        if text and 1 <= len(text) <= 8000:  # 略小于API限制的8192，留出一些余量
            valid_data.append(data)
            valid_texts.append(text)
        else:
            # 如果文本太长，截断它
            if len(text) > 8000:
                truncated_text = text[:8000]
                print(f"警告: 文本过长，已截断至8000字符。原始长度: {len(text)}")
                data[field_name] = truncated_text  # 更新字典中的field_name字段为截断后的文本
                valid_data.append(data)
                valid_texts.append(truncated_text)
            else:
                print(f"警告: 跳过空文本或长度为0的文本")

    if not valid_texts:
        print("错误: 所有文本都无效，无法进行向量化")
        with open(output_file_path, 'w', encoding='utf-8') as outfile:
            json.dump([], outfile, ensure_ascii=False, indent=4)
        return

    # 向量化有效文本 1.6.2
    vectors = vectorize_query(valid_texts)

    # 检查向量化是否成功
    if vectors.size == 0 or len(vectors) != len(valid_data):
        print(
            f"错误: 向量化失败或向量数量({len(vectors) if vectors.size > 0 else 0})与数据条目({len(valid_data)})不匹配")
        # 保存原始数据，但不含向量
        with open(output_file_path, 'w', encoding='utf-8') as outfile:
            json.dump(valid_data, outfile, ensure_ascii=False, indent=4)
        return

    # 添加向量到数据中
    for data, vector in zip(valid_data, vectors): # 每次循环时，data和vector分别是从valid_data和vectors中取出的对应位置的元素
        data['vector'] = vector.tolist()

    # 保存结果
    with open(output_file_path, 'w', encoding='utf-8') as outfile:
        json.dump(valid_data, outfile, ensure_ascii=False, indent=4)

    print(f"成功向量化 {len(valid_data)} 条数据并保存到 {output_file_path}")


# 向量化查询 - 通用函数，被多处使用
def vectorize_query(query, model_name=Config.model_name, batch_size=Config.batch_size) -> np.ndarray:
    """向量化文本查询，返回嵌入向量，改进错误处理和批处理"""
    # 创建OpenAI客户端
    embedding_client = OpenAI(
        api_key=Config.api_key,
        base_url=Config.base_url
    )

    # 如果查询为空，打印警告并返回空数组
    if not query:
        print("警告: 传入向量化的查询为空")
        return np.array([])

    # 如果查询是字符串，将其转换为列表
    if isinstance(query, str):
        query = [query]

    # 验证所有查询文本，确保它们符合API要求
    valid_queries = []
    for q in query:
        # 如果查询为空或不是字符串，打印警告并跳过
        if not q or not isinstance(q, str):
            print(f"警告: 跳过无效查询: {type(q)}")
            continue

        # 清理文本并检查长度
        clean_q = clean_text(q)
        if not clean_q:
            print("警告: 清理后的查询文本为空")
            continue

        # 检查长度是否在API限制范围内
        if len(clean_q) > 8000:
            print(f"警告: 查询文本过长 ({len(clean_q)} 字符)，截断至 8000 字符")
            clean_q = clean_q[:8000]

        valid_queries.append(clean_q)

    # 如果所有查询都无效，打印错误并返回空数组
    if not valid_queries:
        print("错误: 所有查询都无效，无法进行向量化")
        return np.array([])

    # 分批处理有效查询
    all_vectors = []  # 存储所有批次的向量化结果
    for i in range(0, len(valid_queries), batch_size):  # 每次处理10个文本向量
        batch = valid_queries[i:i + batch_size]  # 获取当前批次的文本列表
        try:
            # 记录批次信息便于调试
            print(f"正在向量化批次 {i // batch_size + 1}/{(len(valid_queries) - 1) // batch_size + 1}, "
                  f"包含 {len(batch)} 个文本，第一个文本长度: {len(batch[0][:50])}...")

            # 调用OpenAI API进行向量化
            completion = embedding_client.embeddings.create(
                model=model_name,
                input=batch,
                dimensions=Config.dimensions,
                encoding_format="float"
            )
            vectors = [embedding.embedding for embedding in completion.data]
            all_vectors.extend(vectors)
            print(f"批次 {i // batch_size + 1} 向量化成功，获得 {len(vectors)} 个向量")
        except Exception as e:
            # 如果向量化失败，打印错误信息并跳过该批次
            print(f"向量化批次 {i // batch_size + 1} 失败：{str(e)}")
            print(f"问题批次中的第一个文本: {batch[0][:100]}...")
            traceback.print_exc()
            # 如果是第一批就失败，直接返回空数组
            if i == 0:
                return np.array([])
            # 否则返回已处理的向量
            break

    # 检查是否获得了任何向量
    if not all_vectors:
        print("错误: 向量化过程没有产生任何向量")
        return np.array([])

    return np.array(all_vectors)


# 简单的向量搜索，用于基本对比
def vector_search(query, index_path, metadata_path, limit):
    """基本向量搜索函数"""
    query_vector = vectorize_query(query)
    if query_vector.size == 0:
        return []

    query_vector = np.array(query_vector, dtype=np.float32).reshape(1, -1)

    index = faiss.read_index(index_path)
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    except UnicodeDecodeError:
        print(f"警告：{metadata_path} 包含非法字符，使用 UTF-8 忽略错误重新加载")
        with open(metadata_path, 'rb') as f:
            content = f.read().decode('utf-8', errors='ignore')
            metadata = json.loads(content)

    D, I = index.search(query_vector, limit)
    results = [metadata[i] for i in I[0] if i < len(metadata)]
    return results


# 1.4.2 文本清理
def clean_text(text):
    """清理文本中的非法字符，控制文本长度"""
    if not text:
        return ""
    # 移除控制字符，保留换行和制表符
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    # 移除重复的空白字符
    text = re.sub(r'\s+', ' ', text)
    # 确保文本长度在合理范围内
    return text.strip()


# 1.4.1 PDF文本提取
def extract_text_from_pdf(pdf_path):
    # 尝试打开PDF文件
    try:
        # 使用PyMuPDF库打开PDF文件
        doc = fitz.open(pdf_path)
        text = ""
        # 遍历PDF文件的每一页
        for page in doc:
            # 从每一页中提取文本内容
            page_text = page.get_text()
            # 清理不可打印字符，尝试用 UTF-8 解码，失败时忽略非法字符
            """ 备注：
            从页面提取的文本 page_text 进行编码再解码的操作。目的是尝试将文本统一为
            UTF-8编码，并忽略在转换过程中可能出现的编码错误字符 (errors='ignore')。
            这是一种处理PDF中潜在混合编码或损坏字符的实用方法，虽然可能会丢失少
            量无法正确解码的字符，但能确保程序不会因此崩溃
            """
            text += page_text.encode('utf-8', errors='ignore').decode('utf-8')
        # 如果提取的内容为空，打印警告信息
        if not text.strip():
            print(f"警告：PDF文件 {pdf_path} 提取内容为空")
        return text
    # 捕获异常并打印错误信息
    except Exception as e:
        print(f"PDF文本提取失败：{str(e)}")
        return ""


# 1.4 文本预处理与分块
# 处理单个文件
def process_single_file(file_path: str) -> str:
    """ 处理单个文件
    根据文件扩展名判断是PDF还是普通文本文件，然后提取其内容，并进行编码检测和文本清理
    """
    try:
        if file_path.lower().endswith('.pdf'):
            # 提取PDF内容
            text = extract_text_from_pdf(file_path)
            if not text:
                return f"PDF文件 {file_path} 内容为空或无法提取"
        else:
            # rb表示以二进制模式打开文件
            with open(file_path, "rb") as f:
                content = f.read()
            # 检测文件编码，返回一个字典，其中包含检测到的编码和置信度
            result = chardet.detect(content)
            # 文件编码
            detected_encoding = result['encoding']
            # 置信度(一个介于0和1之间的浮点数，表示检测的准确性)
            confidence = result['confidence']

            # 尝试多种编码方式
            if detected_encoding and confidence > 0.7:
                try:
                    text = content.decode(detected_encoding)  # 使用检测到的编码解码成字符串
                    print(f"文件 {file_path} 使用检测到的编码 {detected_encoding} 解码成功")
                except UnicodeDecodeError:
                    text = content.decode('utf-8', errors='ignore')
                    print(f"文件 {file_path} 使用 {detected_encoding} 解码失败，强制使用 UTF-8 忽略非法字符")
            else:
                # 尝试多种常见编码
                encodings = ['utf-8', 'gbk', 'gb18030', 'gb2312', 'latin-1', 'utf-16', 'cp936', 'big5']
                text = None
                for encoding in encodings:
                    try:
                        text = content.decode(encoding)
                        print(f"文件 {file_path} 使用 {encoding} 解码成功")
                        break
                    except UnicodeDecodeError:
                        continue

                # 如果所有编码都失败，使用忽略错误的方式解码
                if text is None:
                    text = content.decode('utf-8', errors='ignore')
                    print(f"警告：文件 {file_path} 使用 UTF-8 忽略非法字符")

        # 确保文本是干净的，移除非法字符
        text = clean_text(text)
        return text
    except Exception as e:
        print(f"处理文件 {file_path} 时出错: {str(e)}")
        traceback.print_exc()
        return f"处理文件 {file_path} 失败：{str(e)}"


# 批量处理并索引文件 - 修改为支持指定知识库
def process_and_index_files(file_objs: List, kb_name: str = DEFAULT_KB) -> str:
    """接收一个文件对象列表（通常来自Gradio的文件上传组件）和目标知识库名称，然后执行完整的文件处理、分块、向量化和索引构建流程。
        file_objs:文件列表
        kb_name:知识库名称
    """
    # 确保知识库目录存在
    kb_dir = os.path.join(KB_BASE_DIR, kb_name)
    os.makedirs(kb_dir, exist_ok=True)

    # 设置临时处理文件路径
    semantic_chunk_output = os.path.join(OUTPUT_DIR, "semantic_chunk_output.json")
    semantic_chunk_vector = os.path.join(OUTPUT_DIR, "semantic_chunk_vector.json")

    # 设置知识库索引文件路径
    semantic_chunk_index = os.path.join(kb_dir, "semantic_chunk.index")
    semantic_chunk_metadata = os.path.join(kb_dir, "semantic_chunk_metadata.json")

    all_chunks = []
    error_messages = []
    try:
        if not file_objs or len(file_objs) == 0:
            return "错误：没有选择任何文件"

        print(f"开始处理 {len(file_objs)} 个文件，目标知识库: {kb_name}...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_file = {executor.submit(process_single_file, file_obj.name): file_obj for file_obj in file_objs}
            for future in as_completed(future_to_file):
                result = future.result()
                file_obj = future_to_file[future]
                file_name = file_obj.name
                print(f"=======处理文件 {file_name} 结果: {result}====================")

                if isinstance(result, str) and result.startswith("处理文件"):
                    error_messages.append(result)
                    print(result)
                    continue

                # 检查结果是否为有效文本
                if not result or not isinstance(result, str) or len(result.strip()) == 0:
                    error_messages.append(f"文件 {file_name} 处理后内容为空")
                    print(f"警告: 文件 {file_name} 处理后内容为空")
                    continue

                print(f"对文件 {file_name} 进行语义分块...")
                # 1.语义分块
                chunks = semantic_chunk(result)

                if not chunks or len(chunks) == 0:
                    error_messages.append(f"文件 {file_name} 无法生成任何分块")
                    print(f"警告: 文件 {file_name} 无法生成任何分块")
                    continue

                # 2.将处理后的文件保存到知识库目录
                file_basename = os.path.basename(file_name) # 获取不包含路径的文件名称
                dest_file_path = os.path.join(kb_dir, file_basename)# 合并成新路径,join方法自带/,所以不需要再加
                try:
                    shutil.copy2(file_name, dest_file_path) # 将源文件file_basename复制到目标路径dest_file_path，copy2函数会同时复制文件的内容和元数据
                    print(f"已将文件 {file_basename} 复制到知识库 {kb_name}")
                except Exception as e:
                    print(f"复制文件到知识库失败: {str(e)}")

                all_chunks.extend(chunks)
                print(f"文件 {file_name} 处理完成，生成 {len(chunks)} 个分块")

        if not all_chunks:
            return "所有文件处理失败或内容为空\n" + "\n".join(error_messages)

        # 确保分块内容干净且长度合适
        valid_chunks = []
        for chunk in all_chunks:
            # 深度清理文本
            clean_chunk_text = clean_text(chunk["chunk"])

            # 检查清理后的文本是否有效
            if clean_chunk_text and 1 <= len(clean_chunk_text) <= 8000:
                chunk["chunk"] = clean_chunk_text
                valid_chunks.append(chunk)
            elif len(clean_chunk_text) > 8000:
                # 如果文本太长，截断它
                chunk["chunk"] = clean_chunk_text[:8000]
                valid_chunks.append(chunk)
                print(f"警告: 分块 {chunk['id']} 过长已被截断")
            else:
                print(f"警告: 跳过无效分块 {chunk['id']}")

        if not valid_chunks:
            return "所有生成的分块内容无效或为空\n" + "\n".join(error_messages)

        print(f"处理了 {len(all_chunks)} 个分块，有效分块数: {len(valid_chunks)}")

        # 保存语义分块
        with open(semantic_chunk_output, 'w', encoding='utf-8') as json_file:
            json.dump(valid_chunks, json_file, ensure_ascii=False, indent=4)
        print(f"语义分块完成: {semantic_chunk_output}")

        # 向量化语义分块
        print(f"开始向量化 {len(valid_chunks)} 个分块...")
        vectorize_file(valid_chunks, semantic_chunk_vector)
        print(f"语义分块向量化完成: {semantic_chunk_vector}")

        # 验证向量文件是否有效
        try:
            with open(semantic_chunk_vector, 'r', encoding='utf-8') as f:
                vector_data = json.load(f)

            if not vector_data or len(vector_data) == 0:
                return f"向量化失败: 生成的向量文件为空\n" + "\n".join(error_messages)

            # 检查向量数据结构
            if 'vector' not in vector_data[0]:
                return f"向量化失败: 数据中缺少向量字段\n" + "\n".join(error_messages)

            print(f"成功生成 {len(vector_data)} 个向量")
        except Exception as e:
            return f"读取向量文件失败: {str(e)}\n" + "\n".join(error_messages)

        # 构建索引
        print(f"开始为知识库 {kb_name} 构建索引...")
        build_faiss_index(semantic_chunk_vector, semantic_chunk_index, semantic_chunk_metadata)
        print(f"知识库 {kb_name} 索引构建完成: {semantic_chunk_index}")

        status = f"知识库 {kb_name} 更新成功！共处理 {len(valid_chunks)} 个有效分块。\n"
        if error_messages:
            status += "以下文件处理过程中出现问题：\n" + "\n".join(error_messages)
        return status
    except Exception as e:
        error = f"知识库 {kb_name} 索引构建过程中出错：{str(e)}"
        print(error)
        traceback.print_exc()
        return error + "\n" + "\n".join(error_messages)


# 核心联网搜索功能
def get_search_background(query: str, max_length: int = 1500) -> str:
    try:
        from retrievor import q_searching
        search_results = q_searching(query)
        cleaned_results = re.sub(r'\s+', ' ', search_results).strip()
        return cleaned_results[:max_length]
    except Exception as e:
        print(f"联网搜索失败：{str(e)}")
        return ""


# 基本的回答生成
def generate_answer_from_deepseek(question: str, system_prompt: str = "你是一名专业医疗助手，请根据背景知识回答问题。",
                                  background_info: Optional[str] = None) -> str:
    deepseek_client = DeepSeekClient()
    user_prompt = f"问题：{question}"
    if background_info:
        user_prompt = f"背景知识：{background_info}\n\n{user_prompt}"
    try:
        answer = deepseek_client.generate_answer(system_prompt, user_prompt)
        return answer
    except Exception as e:
        return f"生成回答时出错：{str(e)}"


# 多跳推理RAG系统 - 核心创新点
class ReasoningRAG:
    """
    多跳推理RAG系统，通过迭代式的检索和推理过程回答问题，支持流式响应
    """

    def __init__(self,
                 index_path: str,
                 metadata_path: str,
                 max_hops: int = 3,
                 initial_candidates: int = 5,
                 refined_candidates: int = 3,
                 reasoning_model: str = Config.llm_model,
                 verbose: bool = False):
        """
        初始化推理RAG系统
        
        参数:
            index_path: FAISS索引文件的路径
            metadata_path: 元数据JSON文件的路径，与index_path对应
            max_hops: 最大推理-检索跳数
            initial_candidates: 初始检索候选数量,在第一轮（初始）检索时，要检索的候选文本块数量。
            refined_candidates: 精炼检索候选数量,在后续的（精炼）检索轮次中，为每个子查询检索的候选文本块数量。
            reasoning_model: 用于推理步骤的LLM模型
            verbose: 是否打印详细日志
        """
        self.index_path = index_path
        self.metadata_path = metadata_path
        self.max_hops = max_hops
        self.initial_candidates = initial_candidates
        self.refined_candidates = refined_candidates
        self.reasoning_model = reasoning_model
        self.verbose = verbose

        # 加载索引和元数据
        self._load_resources()

    def _load_resources(self):
        """从磁盘中加载FAISS索引和元数据"""
        if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
            self.index = faiss.read_index(self.index_path)
            try:
                with open(self.metadata_path, 'r', encoding='utf-8') as f:
                    self.metadata = json.load(f)
            except UnicodeDecodeError:
                """
                如果加载元数据时发生 UnicodeDecodeError（编码错误），它会尝试
                以二进制模式读取文件，然后用 'utf-8' 编码和 errors='ignore' 选项再次解码。
                这是一种容错机制，用于处理元数据文件中可能存在的非法字符问题。
                """
                with open(self.metadata_path, 'rb') as f:
                    content = f.read().decode('utf-8', errors='ignore')
                    self.metadata = json.loads(content)
        else:
            raise FileNotFoundError(f"Index or metadata not found at {self.index_path} or {self.metadata_path}")

    def _vectorize_query(self, query: str) -> np.ndarray:
        """将查询转换为向量"""
        return vectorize_query(query).reshape(1, -1) # 将得到的向量重新形状为 1 行 n 列的二维数组。-1 表示根据数组的总长度自动计算列数

    def _retrieve(self, query_vector: np.ndarray, limit: int) -> List[Dict[str, Any]]:
        """使用向量相似性检索块"""
        if query_vector.size == 0:
            return []

        # 搜索与查询向量最相似的 limit 个向量,search 方法返回两个数组:
        # D：距离数组，表示每个检索到的向量与查询向量的距离     I:索引数组，表示检索到的向量的索引
        D, I = self.index.search(query_vector, limit)
        results = [self.metadata[i] for i in I[0] if i < len(self.metadata)]
        return results

    def _generate_reasoning(self,
                            query: str,
                            retrieved_chunks: List[Dict[str, Any]],
                            previous_queries: List[str] = None,
                            hop_number: int = 0) -> Dict[str, Any]:
        """
        为检索到的信息生成推理分析并识别信息缺口
        
        返回包含以下字段的字典:
            - analysis: 对当前信息的推理分析
            - missing_info: 已识别的缺失信息
            - follow_up_queries: 填补信息缺口的后续查询列表
            - is_sufficient: 表示信息是否足够的布尔值
        """
        if previous_queries is None:
            previous_queries = []

        # 为模型准备上下文
        chunks_text = "\n\n".join([f"[Chunk {i + 1}]: {chunk['chunk']}"
                                   for i, chunk in enumerate(retrieved_chunks)])

        previous_queries_text = "\n".join([f"Q{i + 1}: {q}" for i, q in enumerate(previous_queries)])

        system_prompt = """
        你是医疗信息检索的专家分析系统。
        你的任务是分析检索到的信息块，识别缺失的内容，并提出有针对性的后续查询来填补信息缺口。
        
        重点关注医疗领域知识，如:
        - 疾病诊断和症状
        - 治疗方法和药物
        - 医学研究和临床试验
        - 患者护理和康复
        - 医疗法规和伦理
        """

        user_prompt = f"""
        ## 原始查询
        {query}
        
        ## 先前查询（如果有）
        {previous_queries_text if previous_queries else "无"}
        
        ## 检索到的信息（跳数 {hop_number}）
        {chunks_text if chunks_text else "未检索到信息。"}
        
        ## 你的任务
        1. 分析已检索到的信息与原始查询的关系
        2. 确定能够更完整回答查询的特定缺失信息
        3. 提出1-3个针对性的后续查询，以检索缺失信息
        4. 确定当前信息是否足够回答原始查询
        
        以JSON格式回答，包含以下字段:
        - analysis: 对当前信息的详细分析
        - missing_info: 特定缺失信息的列表
        - follow_up_queries: 1-3个具体的后续查询
        - is_sufficient: 表示信息是否足够的布尔值
        """

        try:
            response = client.chat.completions.create(
                model=Config.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            )
            reasoning_text = response.choices[0].message.content.strip()

            # 解析JSON响应
            try:
                reasoning = json.loads(reasoning_text)
                # 确保预期的键存在
                required_keys = ["analysis", "missing_info", "follow_up_queries", "is_sufficient"]
                for key in required_keys:
                    if key not in reasoning:
                        reasoning[key] = [] if key != "is_sufficient" else False
                return reasoning
            except json.JSONDecodeError:
                # 如果JSON解析失败，则回退
                if self.verbose:
                    print(f"无法从模型输出解析JSON: {reasoning_text[:100]}...")
                return {
                    "analysis": "无法分析检索到的信息。",
                    "missing_info": ["无法识别缺失信息"],
                    "follow_up_queries": [],
                    "is_sufficient": False
                }

        except Exception as e:
            if self.verbose:
                print(f"推理生成错误: {e}")
                print(traceback.format_exc())
            return {
                "analysis": "分析过程出错。",
                "missing_info": [],
                "follow_up_queries": [],
                "is_sufficient": False
            }

    def _synthesize_answer(self,
                           query: str,
                           all_chunks: List[Dict[str, Any]],
                           reasoning_steps: List[Dict[str, Any]],
                           use_table_format: bool = False) -> str:
        """从所有检索到的块和推理步骤中合成最终答案"""
        # 合并所有块，去除重复
        unique_chunks = []
        chunk_ids = set()
        for chunk in all_chunks:
            if chunk["id"] not in chunk_ids:
                unique_chunks.append(chunk)
                chunk_ids.add(chunk["id"])

        # 准备上下文
        chunks_text = "\n\n".join([f"[Chunk {i + 1}]: {chunk['chunk']}"
                                   for i, chunk in enumerate(unique_chunks)])

        # 准备推理跟踪
        reasoning_trace = ""
        for i, step in enumerate(reasoning_steps):
            reasoning_trace += f"\n\n推理步骤 {i + 1}:\n"
            reasoning_trace += f"分析: {step['analysis']}\n"
            reasoning_trace += f"缺失信息: {', '.join(step['missing_info'])}\n"
            reasoning_trace += f"后续查询: {', '.join(step['follow_up_queries'])}"

        system_prompt = """
        你是医疗领域的专家。基于检索到的信息块，为用户的查询合成一个全面的答案。
        
        重点提供有关医疗和健康的准确、基于证据的信息，包括诊断、治疗、预防和医学研究等方面。
        
        逻辑地组织你的答案，并在适当时引用块中的具体信息。如果信息不完整，请承认限制。
        """

        output_format_instruction = ""
        if use_table_format:
            output_format_instruction = """
            请尽可能以Markdown表格格式组织你的回答。如果信息适合表格形式展示，请使用表格；
            如果不适合表格形式，可以先用文本介绍，然后再使用表格总结关键信息。
            
            表格语法示例：
            | 标题1 | 标题2 | 标题3 |
            | ----- | ----- | ----- |
            | 内容1 | 内容2 | 内容3 |
            
            确保表格格式符合Markdown标准，以便正确渲染。
            """

        user_prompt = f"""
        ## 原始查询
        {query}
        
        ## 检索到的信息块
        {chunks_text}
        
        ## 推理过程
        {reasoning_trace}
        
        ## 你的任务
        使用提供的信息块为原始查询合成一个全面的答案。你的答案应该:
        
        1. 直接回应查询
        2. 结构清晰，易于理解
        3. 基于检索到的信息
        4. 承认可用信息中的任何重大缺口
        
        {output_format_instruction}
        
        以直接回应提出原始查询的用户的方式呈现你的答案。
        """

        try:
            response = client.chat.completions.create(
                model=Config.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if self.verbose:
                print(f"答案合成错误: {e}")
                print(traceback.format_exc())
            return "由于出错，无法生成答案。"

    def stream_retrieve_and_answer(self, query: str, use_table_format: bool = False):
        """
        执行多跳检索和回答生成的流式方法，逐步返回结果
        
        这是一个生成器函数，会在处理的每个阶段产生中间结果
        """
        all_chunks = []
        all_queries = [query]
        reasoning_steps = []

        # 生成状态更新
        yield {
            "status": "正在将查询向量化...",
            "reasoning_display": "",
            "answer": None,
            "all_chunks": [],
            "reasoning_steps": []
        }

        # 初始检索
        try:
            query_vector = self._vectorize_query(query)
            if query_vector.size == 0:
                yield {
                    "status": "向量化失败",
                    "reasoning_display": "由于嵌入错误，无法处理查询。",
                    "answer": "由于嵌入错误，无法处理查询。",
                    "all_chunks": [],
                    "reasoning_steps": []
                }
                return

            yield {
                "status": "正在执行初始检索...",
                "reasoning_display": "",
                "answer": None,
                "all_chunks": [],
                "reasoning_steps": []
            }

            initial_chunks = self._retrieve(query_vector, self.initial_candidates)
            all_chunks.extend(initial_chunks)

            if not initial_chunks:
                yield {
                    "status": "未找到相关信息",
                    "reasoning_display": "未找到与您的查询相关的信息。",
                    "answer": "未找到与您的查询相关的信息。",
                    "all_chunks": [],
                    "reasoning_steps": []
                }
                return

            # 更新状态，展示找到的初始块
            chunks_preview = "\n".join([f"- {chunk['chunk'][:100]}..." for chunk in initial_chunks[:2]])
            yield {
                "status": f"找到 {len(initial_chunks)} 个相关信息块，正在生成初步分析...",
                "reasoning_display": f"### 检索到的初始信息\n{chunks_preview}\n\n### 正在分析...",
                "answer": None,
                "all_chunks": all_chunks,
                "reasoning_steps": []
            }

            # 初始推理
            reasoning = self._generate_reasoning(query, initial_chunks, hop_number=0)
            reasoning_steps.append(reasoning)

            # 生成当前的推理显示
            reasoning_display = "### 多跳推理过程\n"
            reasoning_display += f"**推理步骤 1**\n"
            reasoning_display += f"- 分析: {reasoning['analysis'][:200]}...\n"
            reasoning_display += f"- 缺失信息: {', '.join(reasoning['missing_info'])}\n"
            if reasoning['follow_up_queries']:
                reasoning_display += f"- 后续查询: {', '.join(reasoning['follow_up_queries'])}\n"
            reasoning_display += f"- 信息是否足够: {'是' if reasoning['is_sufficient'] else '否'}\n\n"

            yield {
                "status": "初步分析完成",
                "reasoning_display": reasoning_display,
                "answer": None,
                "all_chunks": all_chunks,
                "reasoning_steps": reasoning_steps
            }

            # 检查是否需要额外的跳数
            hop = 1
            while (hop < self.max_hops and
                   not reasoning["is_sufficient"] and
                   reasoning["follow_up_queries"]):

                follow_up_status = f"执行跳数 {hop}，正在处理 {len(reasoning['follow_up_queries'])} 个后续查询..."
                yield {
                    "status": follow_up_status,
                    "reasoning_display": reasoning_display + f"\n\n### {follow_up_status}",
                    "answer": None,
                    "all_chunks": all_chunks,
                    "reasoning_steps": reasoning_steps
                }

                hop_chunks = []

                # 处理每个后续查询
                for i, follow_up_query in enumerate(reasoning["follow_up_queries"]):
                    all_queries.append(follow_up_query)

                    query_status = f"处理后续查询 {i + 1}/{len(reasoning['follow_up_queries'])}: {follow_up_query}"
                    yield {
                        "status": query_status,
                        "reasoning_display": reasoning_display + f"\n\n### {query_status}",
                        "answer": None,
                        "all_chunks": all_chunks,
                        "reasoning_steps": reasoning_steps
                    }

                    # 为后续查询检索
                    follow_up_vector = self._vectorize_query(follow_up_query)
                    if follow_up_vector.size > 0:
                        follow_up_chunks = self._retrieve(follow_up_vector, self.refined_candidates)
                        hop_chunks.extend(follow_up_chunks)
                        all_chunks.extend(follow_up_chunks)

                        # 更新状态，显示新找到的块数量
                        yield {
                            "status": f"查询 '{follow_up_query}' 找到了 {len(follow_up_chunks)} 个相关块",
                            "reasoning_display": reasoning_display + f"\n\n为查询 '{follow_up_query}' 找到了 {len(follow_up_chunks)} 个相关块",
                            "answer": None,
                            "all_chunks": all_chunks,
                            "reasoning_steps": reasoning_steps
                        }

                # 为此跳数生成推理
                yield {
                    "status": f"正在为跳数 {hop} 生成推理分析...",
                    "reasoning_display": reasoning_display + f"\n\n### 正在为跳数 {hop} 生成推理分析...",
                    "answer": None,
                    "all_chunks": all_chunks,
                    "reasoning_steps": reasoning_steps
                }

                reasoning = self._generate_reasoning(
                    query,
                    hop_chunks,
                    previous_queries=all_queries[:-1],
                    hop_number=hop
                )
                reasoning_steps.append(reasoning)

                # 更新推理显示
                reasoning_display += f"\n**推理步骤 {hop + 1}**\n"
                reasoning_display += f"- 分析: {reasoning['analysis'][:200]}...\n"
                reasoning_display += f"- 缺失信息: {', '.join(reasoning['missing_info'])}\n"
                if reasoning['follow_up_queries']:
                    reasoning_display += f"- 后续查询: {', '.join(reasoning['follow_up_queries'])}\n"
                reasoning_display += f"- 信息是否足够: {'是' if reasoning['is_sufficient'] else '否'}\n"

                yield {
                    "status": f"跳数 {hop} 完成",
                    "reasoning_display": reasoning_display,
                    "answer": None,
                    "all_chunks": all_chunks,
                    "reasoning_steps": reasoning_steps
                }

                hop += 1

            # 合成最终答案
            yield {
                "status": "正在合成最终答案...",
                "reasoning_display": reasoning_display + "\n\n### 正在合成最终答案...",
                "answer": "正在处理您的问题，请稍候...",
                "all_chunks": all_chunks,
                "reasoning_steps": reasoning_steps
            }

            answer = self._synthesize_answer(query, all_chunks, reasoning_steps, use_table_format)

            # 为最终显示准备检索内容汇总
            all_chunks_summary = "\n\n".join([f"**检索块 {i + 1}**:\n{chunk['chunk']}"
                                              for i, chunk in enumerate(all_chunks[:10])])  # 限制显示前10个块

            if len(all_chunks) > 10:
                all_chunks_summary += f"\n\n...以及另外 {len(all_chunks) - 10} 个块（总计 {len(all_chunks)} 个）"

            enhanced_display = reasoning_display + "\n\n### 检索到的内容\n" + all_chunks_summary + "\n\n### 回答已生成"

            yield {
                "status": "回答已生成",
                "reasoning_display": enhanced_display,
                "answer": answer,
                "all_chunks": all_chunks,
                "reasoning_steps": reasoning_steps
            }

        except Exception as e:
            error_msg = f"处理过程中出错: {str(e)}"
            if self.verbose:
                print(error_msg)
                print(traceback.format_exc())

            yield {
                "status": "处理出错",
                "reasoning_display": error_msg,
                "answer": f"处理您的问题时遇到错误: {str(e)}",
                "all_chunks": all_chunks,
                "reasoning_steps": reasoning_steps
            }

    def retrieve_and_answer(self, query: str, use_table_format: bool = False) -> Tuple[str, Dict[str, Any]]:
        """
        执行多跳检索和回答生成的主要方法
        
        返回:
            包含以下内容的元组:
            - 最终答案
            - 包含推理步骤和所有检索到的块的调试字典
        """
        all_chunks = []
        all_queries = [query]
        reasoning_steps = []
        debug_info = {"reasoning_steps": [], "all_chunks": [], "all_queries": all_queries}

        # 初始检索
        query_vector = self._vectorize_query(query)
        if query_vector.size == 0:
            return "由于嵌入错误，无法处理查询。", debug_info

        initial_chunks = self._retrieve(query_vector, self.initial_candidates)
        all_chunks.extend(initial_chunks)
        debug_info["all_chunks"].extend(initial_chunks)

        if not initial_chunks:
            return "未找到与您的查询相关的信息。", debug_info

        # 初始推理
        reasoning = self._generate_reasoning(query, initial_chunks, hop_number=0)
        reasoning_steps.append(reasoning)
        debug_info["reasoning_steps"].append(reasoning)

        # 检查是否需要额外的跳数
        hop = 1
        while (hop < self.max_hops and
               not reasoning["is_sufficient"] and
               reasoning["follow_up_queries"]):

            if self.verbose:
                print(f"开始跳数 {hop}，有 {len(reasoning['follow_up_queries'])} 个后续查询")

            hop_chunks = []

            # 处理每个后续查询
            for follow_up_query in reasoning["follow_up_queries"]:
                all_queries.append(follow_up_query)
                debug_info["all_queries"].append(follow_up_query)

                # 为后续查询检索
                follow_up_vector = self._vectorize_query(follow_up_query)
                if follow_up_vector.size > 0:
                    follow_up_chunks = self._retrieve(follow_up_vector, self.refined_candidates)
                    hop_chunks.extend(follow_up_chunks)
                    all_chunks.extend(follow_up_chunks)
                    debug_info["all_chunks"].extend(follow_up_chunks)

            # 为此跳数生成推理
            reasoning = self._generate_reasoning(
                query,
                hop_chunks,
                previous_queries=all_queries[:-1],
                hop_number=hop
            )
            reasoning_steps.append(reasoning)
            debug_info["reasoning_steps"].append(reasoning)

            hop += 1

        # 合成最终答案
        answer = self._synthesize_answer(query, all_chunks, reasoning_steps, use_table_format)

        return answer, debug_info


# 基于选定知识库生成索引路径
def get_kb_paths(kb_name: str) -> Dict[str, str]:
    """获取指定知识库的索引文件路径"""
    kb_dir = os.path.join(KB_BASE_DIR, kb_name)
    return {
        "index_path": os.path.join(kb_dir, "semantic_chunk.index"),
        "metadata_path": os.path.join(kb_dir, "semantic_chunk_metadata.json")
    }


def multi_hop_generate_answer(query: str, kb_name: str, use_table_format: bool = False,
                              system_prompt: str = "你是一名医疗专家。") -> Tuple[str, Dict]:
    """使用多跳推理RAG生成答案，基于指定知识库"""
    kb_paths = get_kb_paths(kb_name)

    reasoning_rag = ReasoningRAG(
        index_path=kb_paths["index_path"],
        metadata_path=kb_paths["metadata_path"],
        max_hops=3,
        initial_candidates=5,
        refined_candidates=3,
        reasoning_model=Config.llm_model,
        verbose=True
    )

    answer, debug_info = reasoning_rag.retrieve_and_answer(query, use_table_format)
    return answer, debug_info


# 使用简单向量检索生成答案，基于指定知识库
def simple_generate_answer(query: str, kb_name: str, use_table_format: bool = False) -> str:
    """使用简单的向量检索生成答案，不使用多跳推理"""
    try:
        kb_paths = get_kb_paths(kb_name)

        # 使用基本向量搜索
        search_results = vector_search(query, kb_paths["index_path"], kb_paths["metadata_path"], limit=5)

        if not search_results:
            return "未找到相关信息。"

        # 准备背景信息
        background_chunks = "\n\n".join([f"[相关信息 {i + 1}]: {result['chunk']}"
                                         for i, result in enumerate(search_results)])

        # 生成答案
        system_prompt = "你是一名医疗专家。基于提供的背景信息回答用户的问题。"

        if use_table_format:
            system_prompt += "请尽可能以Markdown表格的形式呈现结构化信息。"

        user_prompt = f"""
        问题：{query}
        
        背景信息：
        {background_chunks}
        
        请基于以上背景信息回答用户的问题。
        """

        response = client.chat.completions.create(
            model=Config.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"生成答案时出错：{str(e)}"


# 修改主要的问题处理函数以支持指定知识库
def ask_question_parallel(question: str, kb_name: str = DEFAULT_KB, use_search: bool = True,
                          use_table_format: bool = False, multi_hop: bool = False) -> str:
    """基于指定知识库回答问题"""
    try:
        kb_paths = get_kb_paths(kb_name)
        index_path = kb_paths["index_path"]
        metadata_path = kb_paths["metadata_path"]

        search_background = ""
        local_answer = ""
        debug_info = {}

        # 并行处理
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}

            if use_search:
                futures[executor.submit(get_search_background, question)] = "search"

            if os.path.exists(index_path):
                if multi_hop:
                    # 使用多跳推理
                    futures[executor.submit(multi_hop_generate_answer, question, kb_name, use_table_format)] = "rag"
                else:
                    # 使用简单向量检索
                    futures[executor.submit(simple_generate_answer, question, kb_name, use_table_format)] = "simple"

            for future in as_completed(futures):
                result = future.result()
                if futures[future] == "search":
                    search_background = result or ""
                elif futures[future] == "rag":
                    local_answer, debug_info = result
                elif futures[future] == "simple":
                    local_answer = result

        # 如果同时有搜索和本地结果，合并它们
        if search_background and local_answer:
            system_prompt = "你是一名医疗专家，请整合网络搜索和本地知识库提供全面的解答。"

            table_instruction = ""
            if use_table_format:
                table_instruction = """
                请尽可能以Markdown表格的形式呈现你的回答，特别是对于症状、治疗方法、药物等结构化信息。
                
                请确保你的表格遵循正确的Markdown语法：
                | 列标题1 | 列标题2 | 列标题3 |
                | ------- | ------- | ------- |
                | 数据1   | 数据2   | 数据3   |
                """

            user_prompt = f"""
            问题：{question}
            
            网络搜索结果：{search_background}
            
            本地知识库分析：{local_answer}
            
            {table_instruction}
            
            请根据以上信息，提供一个综合的回答。
            """

            try:
                response = client.chat.completions.create(
                    model="qwen-plus",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ]
                )
                combined_answer = response.choices[0].message.content.strip()
                return combined_answer
            except Exception as e:
                # 如果合并失败，回退到本地答案
                return local_answer
        elif local_answer:
            return local_answer
        elif search_background:
            # 仅从搜索结果生成答案
            system_prompt = "你是一名医疗专家。"
            if use_table_format:
                system_prompt += "请尽可能以Markdown表格的形式呈现结构化信息。"
            return generate_answer_from_deepseek(question, system_prompt=system_prompt,
                                                 background_info=f"[联网搜索结果]：{search_background}")
        else:
            return "未找到相关信息。"

    except Exception as e:
        return f"查询失败：{str(e)}"


# 修改以支持多知识库的流式响应函数
def process_question_with_reasoning(question: str, kb_name: str = DEFAULT_KB, use_search: bool = True,
                                    use_table_format: bool = False, multi_hop: bool = False, chat_history: List = None):
    """增强版process_question，支持流式响应，实时显示检索和推理过程，支持多知识库和对话历史"""
    try:
        kb_paths = get_kb_paths(kb_name)
        index_path = kb_paths["index_path"]
        metadata_path = kb_paths["metadata_path"]

        # 构建带对话历史的问题
        if chat_history and len(chat_history) > 0:
            # 构建对话上下文
            context = "之前的对话内容：\n"
            for user_msg, assistant_msg in chat_history[-3:]:  # 只取最近3轮对话
                context += f"用户：{user_msg}\n"
                context += f"助手：{assistant_msg}\n"
            context += f"\n当前问题：{question}"
            enhanced_question = f"基于以下对话历史，回答用户的当前问题。\n{context}"
        else:
            enhanced_question = question

        # 初始状态
        search_result = "联网搜索进行中..." if use_search else "未启用联网搜索"

        if multi_hop:
            reasoning_status = f"正在准备对知识库 '{kb_name}' 进行多跳推理检索..."
            search_display = f"### 联网搜索结果\n{search_result}\n\n### 推理状态\n{reasoning_status}"
            yield search_display, "正在启动多跳推理流程..."
        else:
            reasoning_status = f"正在准备对知识库 '{kb_name}' 进行向量检索..."
            search_display = f"### 联网搜索结果\n{search_result}\n\n### 检索状态\n{reasoning_status}"
            yield search_display, "正在启动简单检索流程..."

        # 如果启用，并行运行搜索
        search_future = None
        with ThreadPoolExecutor(max_workers=1) as executor:
            if use_search:
                search_future = executor.submit(get_search_background, question)

        # 检查索引是否存在
        if not (os.path.exists(index_path) and os.path.exists(metadata_path)):
            # 如果索引不存在，提前返回
            if search_future:
                # 等待搜索结果
                search_result = "等待联网搜索结果..."
                search_display = f"### 联网搜索结果\n{search_result}\n\n### 检索状态\n知识库 '{kb_name}' 中未找到索引"
                yield search_display, "等待联网搜索结果..."

                search_result = search_future.result() or "未找到相关网络信息"
                system_prompt = "你是一名医疗专家。请考虑对话历史并回答用户的问题。"
                if use_table_format:
                    system_prompt += "请尽可能以Markdown表格的形式呈现结构化信息。"
                answer = generate_answer_from_deepseek(enhanced_question, system_prompt=system_prompt,
                                                       background_info=f"[联网搜索结果]：{search_result}")

                search_display = f"### 联网搜索结果\n{search_result}\n\n### 检索状态\n无法在知识库 '{kb_name}' 中进行本地检索（未找到索引）"
                yield search_display, answer
            else:
                yield f"知识库 '{kb_name}' 中未找到索引，且未启用联网搜索", "无法回答您的问题。请先上传文件到该知识库或启用联网搜索。"
            return

        # 开始流式处理
        current_answer = "正在分析您的问题..."

        if multi_hop:
            # 使用多跳推理的流式接口
            reasoning_rag = ReasoningRAG(
                index_path=index_path,
                metadata_path=metadata_path,
                max_hops=3,
                initial_candidates=5,
                refined_candidates=3,
                verbose=True
            )

            # 使用enhanced_question进行检索
            for step_result in reasoning_rag.stream_retrieve_and_answer(enhanced_question, use_table_format):
                # 更新当前状态
                status = step_result["status"]
                reasoning_display = step_result["reasoning_display"]

                # 如果有新的答案，更新
                if step_result["answer"]:
                    current_answer = step_result["answer"]

                # 如果搜索结果已返回，更新搜索结果
                if search_future and search_future.done():
                    search_result = search_future.result() or "未找到相关网络信息"

                # 构建并返回当前状态
                current_display = f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 推理状态\n{status}\n\n{reasoning_display}"
                yield current_display, current_answer
        else:
            # 简单向量检索的流式处理
            yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 检索状态\n正在执行向量相似度搜索...", "正在检索相关信息..."

            # 执行简单向量搜索，使用enhanced_question
            try:
                search_results = vector_search(enhanced_question, index_path, metadata_path, limit=5)

                if not search_results:
                    yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 检索状态\n未找到相关信息", f"知识库 '{kb_name}' 中未找到相关信息。"
                    current_answer = f"知识库 '{kb_name}' 中未找到相关信息。"
                else:
                    # 显示检索到的信息
                    chunks_detail = "\n\n".join(
                        [f"**相关信息 {i + 1}**:\n{result['chunk']}" for i, result in enumerate(search_results[:5])])
                    chunks_preview = "\n".join(
                        [f"- {result['chunk'][:100]}..." for i, result in enumerate(search_results[:3])])
                    yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 检索状态\n找到 {len(search_results)} 个相关信息块\n\n### 检索到的信息预览\n{chunks_preview}", "正在生成答案..."

                    # 生成答案
                    background_chunks = "\n\n".join([f"[相关信息 {i + 1}]: {result['chunk']}"
                                                     for i, result in enumerate(search_results)])

                    system_prompt = "你是一名医疗专家。基于提供的背景信息和对话历史回答用户的问题。"
                    if use_table_format:
                        system_prompt += "请尽可能以Markdown表格的形式呈现结构化信息。"

                    user_prompt = f"""
                    {enhanced_question}
                    
                    背景信息：
                    {background_chunks}
                    
                    请基于以上背景信息和对话历史回答用户的问题。
                    """

                    response = client.chat.completions.create(
                        model=Config.llm_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ]
                    )

                    current_answer = response.choices[0].message.content.strip()
                    yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 检索状态\n检索完成，已生成答案\n\n### 检索到的内容\n{chunks_detail}", current_answer

            except Exception as e:
                error_msg = f"检索过程中出错: {str(e)}"
                yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 检索状态\n{error_msg}", f"检索过程中出错: {str(e)}"
                current_answer = f"检索过程中出错: {str(e)}"

        # 检索完成后，如果有搜索结果，可以考虑合并知识
        if search_future and search_future.done():
            search_result = search_future.result() or "未找到相关网络信息"

            # 如果同时有搜索结果和本地检索结果，可以考虑合并
            if search_result and current_answer and current_answer not in ["正在分析您的问题...",
                                                                           "本地知识库中未找到相关信息。"]:
                status_text = "正在合并联网搜索和知识库结果..."
                if multi_hop:
                    yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 推理状态\n{status_text}", current_answer
                else:
                    yield f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 检索状态\n{status_text}", current_answer

                # 合并结果
                system_prompt = "你是一名医疗专家，请整合网络搜索和本地知识库提供全面的解答。请考虑对话历史。"

                if use_table_format:
                    system_prompt += "请尽可能以Markdown表格的形式呈现结构化信息。"

                user_prompt = f"""
                {enhanced_question}
                
                网络搜索结果：{search_result}
                
                本地知识库分析：{current_answer}
                
                请根据以上信息和对话历史，提供一个综合的回答。确保使用Markdown表格来呈现适合表格形式的信息。
                """

                try:
                    response = client.chat.completions.create(
                        model="qwen-plus",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ]
                    )
                    combined_answer = response.choices[0].message.content.strip()

                    final_status = "已整合联网和知识库结果"
                    if multi_hop:
                        final_display = f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 本地知识库分析\n已完成多跳推理分析，检索到的内容已在上方显示\n\n### 综合分析\n{final_status}"
                    else:
                        # 获取之前检索到的内容
                        chunks_info = "".join(
                            [part.split("### 检索到的内容\n")[-1] if "### 检索到的内容\n" in part else "" for part in
                             search_display.split("### 联网搜索结果")])
                        if not chunks_info.strip():
                            chunks_info = "检索内容已在上方显示"
                        final_display = f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 本地知识库分析\n已完成向量检索分析\n\n### 检索到的内容\n{chunks_info}\n\n### 综合分析\n{final_status}"

                    yield final_display, combined_answer
                except Exception as e:
                    # 如果合并失败，使用现有答案
                    error_status = f"合并结果失败: {str(e)}"
                    if multi_hop:
                        final_display = f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 本地知识库分析\n已完成多跳推理分析，检索到的内容已在上方显示\n\n### 综合分析\n{error_status}"
                    else:
                        # 获取之前检索到的内容
                        chunks_info = "".join(
                            [part.split("### 检索到的内容\n")[-1] if "### 检索到的内容\n" in part else "" for part in
                             search_display.split("### 联网搜索结果")])
                        if not chunks_info.strip():
                            chunks_info = "检索内容已在上方显示"
                        final_display = f"### 联网搜索结果\n{search_result}\n\n### 知识库: {kb_name}\n### 本地知识库分析\n已完成向量检索分析\n\n### 检索到的内容\n{chunks_info}\n\n### 综合分析\n{error_status}"

                    yield final_display, current_answer

    except Exception as e:
        error_msg = f"处理失败：{str(e)}\n{traceback.format_exc()}"
        yield f"### 错误信息\n{error_msg}", f"处理您的问题时遇到错误：{str(e)}"


# 添加处理函数，批量上传文件到指定知识库
def batch_upload_to_kb(file_objs: List, kb_name: str) -> str:
    """批量上传文件到指定知识库并进行处理"""
    try:
        if not kb_name or not kb_name.strip():
            return "错误：未指定知识库"

        # 确保知识库目录存在
        kb_dir = os.path.join(KB_BASE_DIR, kb_name)
        if not os.path.exists(kb_dir):
            os.makedirs(kb_dir, exist_ok=True)

        if not file_objs or len(file_objs) == 0:
            return "错误：未选择任何文件"

        return process_and_index_files(file_objs, kb_name)
    except Exception as e:
        return f"上传文件到知识库失败: {str(e)}"


# Gradio 界面 - 修改为支持多知识库
custom_css = """
.web-search-toggle .form { display: flex !important; align-items: center !important; }
.web-search-toggle .form > label { order: 2 !important; margin-left: 10px !important; }
.web-search-toggle .checkbox-wrap { order: 1 !important; background: #d4e4d4 !important; border-radius: 15px !important; padding: 2px !important; width: 50px !important; height: 28px !important; }
.web-search-toggle .checkbox-wrap .checkbox-container { width: 24px !important; height: 24px !important; transition: all 0.3s ease !important; }
.web-search-toggle input:checked + .checkbox-wrap { background: #2196F3 !important; }
.web-search-toggle input:checked + .checkbox-wrap .checkbox-container { transform: translateX(22px) !important; }
#search-results { max-height: 400px; overflow-y: auto; border: 1px solid #2196F3; border-radius: 5px; padding: 10px; background-color: #e7f0f9; }
#question-input { border-color: #2196F3 !important; }
#answer-output { background-color: #f0f7f0; border-color: #2196F3 !important; max-height: 400px; overflow-y: auto; }
.submit-btn { background-color: #2196F3 !important; border: none !important; }
.reasoning-steps { background-color: #f0f7f0; border: 1px dashed #2196F3; padding: 10px; margin-top: 10px; border-radius: 5px; }
.loading-spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid rgba(33, 150, 243, 0.3); border-radius: 50%; border-top-color: #2196F3; animation: spin 1s ease-in-out infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.stream-update { animation: fade 0.5s ease-in-out; }
@keyframes fade { from { background-color: rgba(33, 150, 243, 0.1); } to { background-color: transparent; } }
.status-box { padding: 10px; border-radius: 5px; margin-bottom: 10px; font-weight: bold; }
.status-processing { background-color: #e3f2fd; color: #1565c0; border-left: 4px solid #2196F3; }
.status-success { background-color: #e8f5e9; color: #2e7d32; border-left: 4px solid #4CAF50; }
.status-error { background-color: #ffebee; color: #c62828; border-left: 4px solid #f44336; }
.multi-hop-toggle .form { display: flex !important; align-items: center !important; }
.multi-hop-toggle .form > label { order: 2 !important; margin-left: 10px !important; }
.multi-hop-toggle .checkbox-wrap { order: 1 !important; background: #d4e4d4 !important; border-radius: 15px !important; padding: 2px !important; width: 50px !important; height: 28px !important; }
.multi-hop-toggle .checkbox-wrap .checkbox-container { width: 24px !important; height: 24px !important; transition: all 0.3s ease !important; }
.multi-hop-toggle input:checked + .checkbox-wrap { background: #4CAF50 !important; }
.multi-hop-toggle input:checked + .checkbox-wrap .checkbox-container { transform: translateX(22px) !important; }
.kb-management { border: 1px solid #2196F3; border-radius: 5px; padding: 15px; margin-bottom: 15px; background-color: #f0f7ff; }
.kb-selector { margin-bottom: 10px; }
/* 缩小文件上传区域高度 */
.compact-upload {
    margin-bottom: 10px;
}

.file-upload.compact {
    padding: 10px;  /* 减小内边距 */
    min-height: 120px; /* 减小最小高度 */
    margin-bottom: 10px;
}

/* 优化知识库内容显示区域 */
.kb-files-list {
    height: 400px;
    overflow-y: auto;
}

/* 确保右侧列有足够空间 */
#kb-files-group {
    height: 100%;
    display: flex;
    flex-direction: column;
}
.kb-files-list { max-height: 250px; overflow-y: auto; border: 1px solid #ccc; border-radius: 5px; padding: 10px; margin-top: 10px; background-color: #f9f9f9; }
#kb-management-container {
    max-width: 800px !important;
    margin: 0 !important; /* 移除自动边距，靠左对齐 */
    margin-left: 20px !important; /* 添加左边距 */
}
.container {
    max-width: 1200px !important;
    margin: 0 auto !important;
}
.file-upload {
    border: 2px dashed #2196F3;
    padding: 15px;
    border-radius: 10px;
    background-color: #f0f7ff;
    margin-bottom: 15px;
}
.tabs.tab-selected {
    background-color: #e3f2fd;
    border-bottom: 3px solid #2196F3;
}
.group {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 10px;
    margin-bottom: 15px;
    background-color: #fafafa;
}

/* 添加更多针对知识库管理页面的样式 */
#kb-controls, #kb-file-upload, #kb-files-group {
    width: 100% !important;
    max-width: 800px !important;
    margin-right: auto !important;
}

/* 修改Gradio默认的标签页样式以支持左对齐 */
.tabs > .tab-nav > button {
    flex: 0 1 auto !important; /* 修改为不自动扩展，只占用必要空间 */
}
.tabs > .tabitem {
    padding-left: 0 !important; /* 移除左边距，使内容靠左 */
}
/* 对于首页的顶部标题部分 */
#app-container h1, #app-container h2, #app-container h3, 
#app-container > .prose {
    text-align: left !important;
    padding-left: 20px !important;
}
"""

custom_theme = gr.themes.Soft(
    primary_hue="blue",
    secondary_hue="blue",
    neutral_hue="gray",
    text_size="lg",
    spacing_size="md",
    radius_size="md"
)

# 添加简单的JavaScript，通过html组件实现
js_code = """
<script>
document.addEventListener('DOMContentLoaded', function() {
    // 当页面加载完毕后，找到提交按钮，并为其添加点击事件
    const observer = new MutationObserver(function(mutations) {
        // 找到提交按钮
        const submitButton = document.querySelector('button[data-testid="submit"]');
        if (submitButton) {
            submitButton.addEventListener('click', function() {
                // 找到检索标签页按钮并点击它
                setTimeout(function() {
                    const retrievalTab = document.querySelector('[data-testid="tab-button-retrieval-tab"]');
                    if (retrievalTab) retrievalTab.click();
                }, 100);
            });
            observer.disconnect(); // 一旦找到并设置事件，停止观察
        }
    });
    
    // 开始观察文档变化
    observer.observe(document.body, { childList: true, subtree: true });
});
</script>
"""

with gr.Blocks(title="医疗知识问答系统", theme=custom_theme, css=custom_css, elem_id="app-container") as demo:
    with gr.Column(elem_id="header-container"):
        gr.Markdown("""
        # 🏥 医疗知识问答系统
        **智能医疗助手，支持多知识库管理、多轮对话、普通语义检索和高级多跳推理**  
        本系统支持创建多个知识库，上传TXT或PDF文件，通过语义向量检索或创新的多跳推理机制提供医疗信息查询服务。
        """)

    # 添加JavaScript脚本
    gr.HTML(js_code, visible=False)

    # 使用State来存储对话历史
    chat_history_state = gr.State([])

    # 创建标签页
    with gr.Tabs() as tabs:
        # 知识库管理标签页
        with gr.TabItem("知识库管理"):
            with gr.Row():
                # 左侧列：控制区
                with gr.Column(scale=1, min_width=400):
                    gr.Markdown("### 📚 知识库管理与构建")

                    with gr.Row(elem_id="kb-controls"):
                        with gr.Column(scale=1):
                            new_kb_name = gr.Textbox(
                                label="新知识库名称",
                                placeholder="输入新知识库名称",
                                lines=1
                            )
                            create_kb_btn = gr.Button("创建知识库", variant="primary", scale=1)

                        with gr.Column(scale=1):
                            current_kbs = get_knowledge_bases()
                            kb_dropdown = gr.Dropdown(
                                label="选择知识库",
                                choices=current_kbs,
                                value=DEFAULT_KB if DEFAULT_KB in current_kbs else (
                                    current_kbs[0] if current_kbs else None),
                                elem_classes="kb-selector"
                            )

                            with gr.Row():
                                refresh_kb_btn = gr.Button("刷新列表", size="sm", scale=1)
                                delete_kb_btn = gr.Button("删除知识库", size="sm", variant="stop", scale=1)

                    kb_status = gr.Textbox(label="知识库状态", interactive=False, placeholder="选择或创建知识库")

                    with gr.Group(elem_id="kb-file-upload", elem_classes="compact-upload"):
                        gr.Markdown("### 📄 上传文件到知识库")
                        file_upload = gr.File(
                            label="选择文件（支持多选TXT/PDF）",
                            type="filepath",
                            file_types=[".txt", ".pdf"],
                            file_count="multiple",
                            elem_classes="file-upload compact"
                        )
                        upload_status = gr.Textbox(label="上传状态", interactive=False, placeholder="上传后显示状态")

                    kb_select_for_chat = gr.Dropdown(
                        label="为对话选择知识库",
                        choices=current_kbs,
                        value=DEFAULT_KB if DEFAULT_KB in current_kbs else (current_kbs[0] if current_kbs else None),
                        visible=False  # 隐藏，仅用于同步
                    )

                with gr.Column(scale=1, min_width=400):
                    with gr.Group(elem_id="kb-files-group"):
                        gr.Markdown("### 📋 知识库内容")
                        kb_files_list = gr.Markdown(
                            value="选择知识库查看文件...",
                            elem_classes="kb-files-list"
                        )

                # 用于对话界面的知识库选择器
                kb_select_for_chat = gr.Dropdown(
                    label="为对话选择知识库",
                    choices=current_kbs,
                    value=DEFAULT_KB if DEFAULT_KB in current_kbs else (current_kbs[0] if current_kbs else None),
                    visible=False  # 隐藏，仅用于同步
                )

        # 对话交互标签页
        with gr.TabItem("对话交互"):
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### ⚙️ 对话设置")

                    kb_dropdown_chat = gr.Dropdown(
                        label="选择知识库进行对话",
                        choices=current_kbs,
                        value=DEFAULT_KB if DEFAULT_KB in current_kbs else (current_kbs[0] if current_kbs else None),
                    )

                    with gr.Row():
                        web_search_toggle = gr.Checkbox(
                            label="🌐 启用联网搜索",
                            value=True,
                            info="获取最新医疗动态",
                            elem_classes="web-search-toggle"
                        )
                        table_format_toggle = gr.Checkbox(
                            label="📊 表格格式输出",
                            value=True,
                            info="使用Markdown表格展示结构化回答",
                            elem_classes="web-search-toggle"
                        )

                    multi_hop_toggle = gr.Checkbox(
                        label="🔄 启用多跳推理",
                        value=False,
                        info="使用高级多跳推理机制（较慢但更全面）",
                        elem_classes="multi-hop-toggle"
                    )

                    with gr.Accordion("显示检索进展", open=False):
                        search_results_output = gr.Markdown(
                            label="检索过程",
                            elem_id="search-results",
                            value="等待提交问题..."
                        )

                with gr.Column(scale=3):
                    gr.Markdown("### 💬 对话历史")
                    chatbot = gr.Chatbot(
                        elem_id="chatbot",
                        label="对话历史",
                        height=550
                    )

            with gr.Row():
                question_input = gr.Textbox(
                    label="输入医疗健康相关问题",
                    placeholder="例如：2型糖尿病的症状和治疗方法有哪些？",
                    lines=2,
                    elem_id="question-input"
                )

            with gr.Row(elem_classes="submit-row"):
                submit_btn = gr.Button("提交问题", variant="primary", elem_classes="submit-btn")
                clear_btn = gr.Button("清空输入", variant="secondary")
                clear_history_btn = gr.Button("清空对话历史", variant="secondary", elem_classes="clear-history-btn")

            # 状态显示框
            status_box = gr.HTML(
                value='<div class="status-box status-processing">准备就绪，等待您的问题</div>',
                visible=True
            )

            gr.Examples(
                examples=[
                    ["2型糖尿病的症状和治疗方法有哪些？"],
                    ["高血压患者的日常饮食应该注意什么？"],
                    ["肺癌的早期症状和筛查方法是什么？"],
                    ["新冠肺炎后遗症有哪些表现？如何缓解？"],
                    ["儿童过敏性鼻炎的诊断标准和治疗方案有哪些？"]
                ],
                inputs=question_input,
                label="示例问题（点击尝试）"
            )


    # 创建知识库函数
    def create_kb_and_refresh(kb_name):
        result = create_knowledge_base(kb_name)
        kbs = get_knowledge_bases()
        # 更新两个下拉菜单
        return result, gr.update(choices=kbs, value=kb_name if "创建成功" in result else None), gr.update(choices=kbs,
                                                                                                          value=kb_name if "创建成功" in result else None)


    # 刷新知识库列表
    def refresh_kb_list():
        kbs = get_knowledge_bases()
        # 更新两个下拉菜单
        return gr.update(choices=kbs, value=kbs[0] if kbs else None), gr.update(choices=kbs,
                                                                                value=kbs[0] if kbs else None)


    # 删除知识库
    def delete_kb_and_refresh(kb_name):
        result = delete_knowledge_base(kb_name)
        kbs = get_knowledge_bases()
        # 更新两个下拉菜单
        return result, gr.update(choices=kbs, value=kbs[0] if kbs else None), gr.update(choices=kbs,
                                                                                        value=kbs[0] if kbs else None)


    # 更新知识库文件列表
    def update_kb_files_list(kb_name):
        if not kb_name:
            return "未选择知识库"

        files = get_kb_files(kb_name)
        kb_dir = os.path.join(KB_BASE_DIR, kb_name)
        has_index = os.path.exists(os.path.join(kb_dir, "semantic_chunk.index"))

        if not files:
            files_str = "知识库中暂无文件"
        else:
            files_str = "**文件列表:**\n\n" + "\n".join([f"- {file}" for file in files])

        index_status = "\n\n**索引状态:** " + ("✅ 已建立索引" if has_index else "❌ 未建立索引")

        return f"### 知识库: {kb_name}\n\n{files_str}{index_status}"


    # 同步知识库选择 - 管理界面到对话界面
    def sync_kb_to_chat(kb_name):
        return gr.update(value=kb_name)


    # 同步知识库选择 - 对话界面到管理界面
    def sync_chat_to_kb(kb_name):
        return gr.update(value=kb_name), update_kb_files_list(kb_name)


    # 处理文件上传到指定知识库
    def process_upload_to_kb(files, kb_name):
        if not kb_name:
            return "错误：未选择知识库"

        result = batch_upload_to_kb(files, kb_name)
        # 更新知识库文件列表
        files_list = update_kb_files_list(kb_name)
        return result, files_list


    # 知识库选择变化时
    def on_kb_change(kb_name):
        if not kb_name:
            return "未选择知识库", "选择知识库查看文件..."

        kb_dir = os.path.join(KB_BASE_DIR, kb_name)
        has_index = os.path.exists(os.path.join(kb_dir, "semantic_chunk.index"))
        status = f"已选择知识库: {kb_name}" + (" (已建立索引)" if has_index else " (未建立索引)")

        # 更新文件列表
        files_list = update_kb_files_list(kb_name)

        return status, files_list


    # 创建知识库按钮功能
    create_kb_btn.click(
        fn=create_kb_and_refresh,
        inputs=[new_kb_name],
        outputs=[kb_status, kb_dropdown, kb_dropdown_chat]
    ).then(
        fn=lambda: "",  # 清空输入框
        inputs=[],
        outputs=[new_kb_name]
    )

    # 刷新知识库列表按钮功能
    refresh_kb_btn.click(
        fn=refresh_kb_list,
        inputs=[],
        outputs=[kb_dropdown, kb_dropdown_chat]
    )

    # 删除知识库按钮功能
    delete_kb_btn.click(
        fn=delete_kb_and_refresh,
        inputs=[kb_dropdown],
        outputs=[kb_status, kb_dropdown, kb_dropdown_chat]
    ).then(
        fn=update_kb_files_list,
        inputs=[kb_dropdown],
        outputs=[kb_files_list]
    )

    # 知识库选择变化时 - 管理界面
    kb_dropdown.change(
        fn=on_kb_change,
        inputs=[kb_dropdown],
        outputs=[kb_status, kb_files_list]
    ).then(
        fn=sync_kb_to_chat,
        inputs=[kb_dropdown],
        outputs=[kb_dropdown_chat]
    )

    # 知识库选择变化时 - 对话界面
    kb_dropdown_chat.change(
        fn=sync_chat_to_kb,
        inputs=[kb_dropdown_chat],
        outputs=[kb_dropdown, kb_files_list]
    )

    # 处理文件上传
    file_upload.upload(
        fn=process_upload_to_kb,
        inputs=[file_upload, kb_dropdown],
        outputs=[upload_status, kb_files_list]
    )

    # 清空输入按钮功能
    clear_btn.click(
        fn=lambda: "",
        inputs=[],
        outputs=[question_input]
    )


    # 清空对话历史按钮功能
    def clear_history():
        return [], []


    clear_history_btn.click(
        fn=clear_history,
        inputs=[],
        outputs=[chatbot, chat_history_state]
    )


    # 提交按钮 - 开始流式处理
    def update_status(is_processing=True, is_error=False):
        if is_processing:
            return '<div class="status-box status-processing">正在处理您的问题...</div>'
        elif is_error:
            return '<div class="status-box status-error">处理过程中出现错误</div>'
        else:
            return '<div class="status-box status-success">回答已生成完毕</div>'


    # 处理问题并更新对话历史
    def process_and_update_chat(question, kb_name, use_search, use_table_format, multi_hop, chat_history):
        if not question.strip():
            return chat_history, update_status(False, True), "等待提交问题..."

        try:
            # 首先更新聊天界面，显示用户问题
            chat_history.append([question, "正在思考..."])
            yield chat_history, update_status(True), f"开始处理您的问题，使用知识库: {kb_name}..."

            # 用于累积检索状态和答案
            last_search_display = ""
            last_answer = ""

            # 使用生成器进行流式处理
            for search_display, answer in process_question_with_reasoning(question, kb_name, use_search,
                                                                          use_table_format, multi_hop,
                                                                          chat_history[:-1]):
                # 更新检索状态和答案
                last_search_display = search_display
                last_answer = answer

                # 更新聊天历史中的最后一条（当前的回答）
                if chat_history:
                    chat_history[-1][1] = answer
                    yield chat_history, update_status(True), search_display

            # 处理完成，更新状态
            yield chat_history, update_status(False), last_search_display

        except Exception as e:
            # 发生错误时更新状态和聊天历史
            error_msg = f"处理问题时出错: {str(e)}"
            if chat_history:
                chat_history[-1][1] = error_msg
            yield chat_history, update_status(False, True), f"### 错误\n{error_msg}"


    # 连接提交按钮
    submit_btn.click(
        fn=process_and_update_chat,
        inputs=[question_input, kb_dropdown_chat, web_search_toggle, table_format_toggle, multi_hop_toggle,
                chat_history_state],
        outputs=[chatbot, status_box, search_results_output],
        queue=True
    ).then(
        fn=lambda: "",  # 清空输入框
        inputs=[],
        outputs=[question_input]
    ).then(
        fn=lambda h: h,  # 更新state
        inputs=[chatbot],
        outputs=[chat_history_state]
    )

    # 支持Enter键提交
    question_input.submit(
        fn=process_and_update_chat,
        inputs=[question_input, kb_dropdown_chat, web_search_toggle, table_format_toggle, multi_hop_toggle,
                chat_history_state],
        outputs=[chatbot, status_box, search_results_output],
        queue=True
    ).then(
        fn=lambda: "",  # 清空输入框
        inputs=[],
        outputs=[question_input]
    ).then(
        fn=lambda h: h,  # 更新state
        inputs=[chatbot],
        outputs=[chat_history_state]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, share=True)
