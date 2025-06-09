import requests
import json
import re
from lxml import etree
import chardet
import jieba.analyse
from text2vec import *
from config import Config

def search_bing(query):
    """利用newbing搜索接口，用于检索与query相关的背景信息，作为检索内容
    input：query
    output：{'url':'','text':'','title':''}
    """
    headers = {
        'Cookie': 'MUID=2CFCFC26663D64393955ED1C623D62A4; MUIDB=2CFCFC26663D64393955ED1C623D62A4; SRCHD=AF=S00028; SRCHUID=V=2&GUID=76DC1CA8309043BBAB81CFC4C47D76DD&dmnchg=1; _UR=QS=0&TQS=0; MicrosoftApplicationsTelemetryDeviceId=64c1979f-ee59-40a7-928e-b755865bc6ae; ABDEF=V=13&ABDV=13&MRNB=1696643925014&MRB=0; ANON=A=15BC3EC2F3AC041DAD2C715CFFFFFFFF&E=1d05&W=2; NAP=V=1.9&E=1cab&C=MnJiRko1YRJfqV6H22giKijH0-4G1Ub50-Cg7gnMPMN4QFF_OeDZsQ&W=2; PPLState=1; _HPVN=CS=eyJQbiI6eyJDbiI6NiwiU3QiOjAsIlFzIjowLCJQcm9kIjoiUCJ9LCJTYyI6eyJDbiI6NiwiU3QiOjAsIlFzIjowLCJQcm9kIjoiSCJ9LCJReiI6eyJDbiI6NiwiU3QiOjAsIlFzIjowLCJQcm9kIjoiVCJ9LCJBcCI6dHJ1ZSwiTXV0ZSI6dHJ1ZSwiTGFkIjoiMjAyMy0xMi0xMVQwMDowMDowMFoiLCJJb3RkIjowLCJHd2IiOjAsIlRucyI6MCwiRGZ0IjpudWxsLCJNdnMiOjAsIkZsdCI6MCwiSW1wIjoxNiwiVG9iYnMiOjB9; _EDGE_S=SID=2E9C7DC6F71A6094195D6E28F6C8614B; USRLOC=HS=1&ELOC=LAT=30.26828956604004|LON=120.13351440429688|N=%E8%A5%BF%E6%B9%96%E5%8C%BA%EF%BC%8C%E6%B5%99%E6%B1%9F%E7%9C%81|ELT=4|; _Rwho=u=d; _SS=SID=2E9C7DC6F71A6094195D6E28F6C8614B&R=64&RB=64&GB=0&RG=0&RP=61; KievRPSSecAuth=FABKBBRaTOJILtFsMkpLVWSG6AN6C/svRwNmAAAEgAAACFAPF0afImbrCASVm1xT1K+FiXTsQgaoq6RydL+Ge3FvFrIbHVbXa7m0OlZNQJT4P62pu6xUtDTqwGPUE13tWBwVPkK1RahHVaGuUSLfwbp5o2HeLnKa+hfc6+sJiYHnxklhiJAzdi/oBbiWdDkf+5A+C0Fbsxeo4pQDt+kmeKhWpMwijA0bVP5ISXdkrLsRv5jiq97srkAMWFHqqGboI70LdX7ahqSSiykzwFdA1np3WhYhURWQ4b3z6uV7nsZpth6lpdafGZ2YLWr0Zwpv1D210P04ovzbbzvuKGoeljS4/SvdX8QUoGONzn0f2OXAOPvsnZJctbwxH/tkembDlpN4liJDCYhlYgoKtg5nuLBNihk75VctLodAQhosDNYM9stJRzQlusK+aEbDQKAgXunPwB0iPq0ECEVmLIApOeXs7DEtj29Q8zuWiOmxXnddGDm4Tf0VWUVjAEfP/PKiiTLAAS/dwPgOslgEdpy3Pw6GQYo3z3dZ16mWuXYX53utgdkK4rtqRj/FmYiTRjL6scm7Ds0UJnVNxdJcFACadTOzNVEGBp2XIb6XEAWZThz21+JJCn325RXG+zwJyjaKI941n6CbQ8Z/dXgUYMBsn/gfdGV3/+vz05pIOtB1zmzkvwds5v4M/zTcf5fgqWwLjSbIBFscYA626llQwDS6LkKwyoB/EB3L0XgLnOFpoSSpk41L/q5e0GkLVxzPA5kZue0iLTNEXUu/KCGnPOBkK0iAZVS/bJPVa3ZBPBOODwXnAUR0s0W1hbHLDW4I1ZrMuujx08DU0/nhhiq0mFgwwxHrd4vE9xdecjlpyL78pzPf5LVAiCKaWZ/BnKqHCYHA4hiEg8ffC5eFwoA6JsL0wtvTSdaAPEcUs103Um9eje8nNKwvDtesqh93lOAbNCfkfC/zAdtsR0dWaZIsYdAeMNQE+6//zLDbGIe24WVsSdiwZqdmYI2ICxE+KqPY++Ei4gfgKt0GNyiAfK0qSfALb01POx95rWOyWSPd0ge6DwM5mHAZfTePR44vBfFdhvUYBg0+47nOzY53hcO/6unDb3u1/PLHM7+rlS+76yjrZ9Dl7cFXRNBINy5afDUY+kU9JQS6QTbc5EIQTynlWkGU38m43GtWXquitzrjHuC0mYsUbLQOuZ1kFWHQXF/4I/aaxU1k0uvEOttkIUkhXi5lKo9uLoPGdha+AIGcDz8icpdDnfAHHpChm0YB8K8lcL0foY6NCib+o+LCLfriZg9Nvtkc8s1+TWPvCvHZX4bZuXyN4tHoQiysRd6j0gyJpLR4yQr5iOyBUgkM9WWKzkFmnzVYlb4ec6wpowsw2643AHs5Ge1FDjzKw3TdSVnwB2dHFh7tdNW1ywYDAGhpv8SSvQ66448UANVqB1uKwxsD0mXJR/tjMy9OuiNR; _U=1S7ve-XVb_pOh5Iig5kQlQDI6wv9BNl9HiCEtz0dS6dNV_UrQUBmAFVEZx7pYNRTwRxGG8eASH_IDUlpJu04SCp8aeYlPHkU_-0xGzlVA3nTqaE9kSUyIm1UVQYovjbOrsh4SeBbU-wrjqz6HV2DeUKJiHyTwYlDeQ8bYboyqhB4-ER5PjMGcp8daGbur9ER2KSm-nJOeUqnWeIawk0BVyw; WLS=C=26d7831f7a57e7fd&N=; SRCHUSR=DOB=20220207&T=1703137884000&TPC=1703137888000&POEX=W; SRCHHPGUSR=SRCHLANG=zh-Hans&BZA=0&BRW=HTP&BRH=M&CW=975&CH=931&SW=1920&SH=1080&DPR=1&UTC=480&DM=0&HV=1703137888&WTS=63838734684&PRVCW=975&PRVCH=931&SCW=1164&SCH=2821&PV=10.0.0; WLID=mA9cZwKIoBbQ9h07pbmfeYJEn7iBxd5sk7A9mKFJf1dP4SWmtri4X9d1xcl06hKEVmEpT+5GB21NeHYv/uk3maNbHalTEB+UwCwfS7RdzoQ=; _RwBf=r=0&ilt=1&ihpd=1&ispd=0&rc=64&rb=64&gb=0&rg=0&pc=61&mtu=0&rbb=0.0&g=0&cid=&clo=0&v=1&l=2023-12-20T08:00:00.0000000Z&lft=0001-01-01T00:00:00.0000000&aof=0&o=0&p=bingcopilotwaitlist&c=MY00IA&t=3001&s=2023-03-20T09:14:27.6545941+00:00&ts=2023-12-21T05:51:26.7082437+00:00&rwred=0&wls=2&wlb=0&lka=0&lkt=0&aad=0&TH=&mta=0&e=CS-LRz6MT6YjyZDqHmn2zXGq0iVnD2Plg7iI7uA3t-iwF4TTPdW2rejPh5N_c6syhuNr1-uNgqm8vKVLqjaaig&A=15BC3EC2F3AC041DAD2C715CFFFFFFFF&wle=1&ccp=0&ard=0001-01-01T00:00:00.0000000; ipv6=hit=1703141490169&t=4',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept': '*/*',
        'Referer': 'https://cn.bing.com/search?',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        }

    res = []
    url = 'https://cn.bing.com/search?q=' + query + '&qs=n&form=QBRE'
    r = requests.get(url, headers=headers)
    try:
        encoding = chardet.detect(r.content)['encoding']
        r.encoding = encoding
        dom = etree.HTML(r.content.decode(encoding))
    except:
        dom = etree.HTML(r.content)

    url_list = []
    tmp_url = []
    #只采集列表的第一页
    for sel in dom.xpath('//ol[@id="b_results"]/li/h2'):
        l = ''.join(sel.xpath('a/@href'))
        title = ''.join(sel.xpath('a//text()')).split('-')[0].strip()
        if 'http' in l and l not in tmp_url and 'doc.' not in l:
            url_list.append([l,title])
            tmp_url.append(l)
    for turl,title in url_list:
        try:
            tr = requests.get(turl, headers=headers, timeout=(5, 5))
            tdom = etree.HTML(tr.content.decode('utf-8'))
            text = '\n'.join(tdom.xpath('//p/text()'))
            if len(text) > 15:
                tmp = {}
                tmp['url'] = turl
                tmp['text'] = text
                tmp['title'] = title
                res.append(tmp)
        except Exception as e:
            print(e)
            pass
    return res




class TextRecallRank():
    """
    实现对检索内容的召回与排序
    """

    def __init__(self,cfg):
        self.topk = cfg.topk    #query关键词召回的数量
        self.topd = cfg.topd    #召回文章的数量
        self.topt = cfg.topt    #召回文本片段的数量
        self.maxlen = cfg.maxlen  #召回文本片段的长度
        self.recall_way = cfg.recall_way  #召回方式



    def query_analyze(self,query):
        """query的解析，目前利用jieba进行关键词提取
        input:query,topk
        output:
            keywords:{'word':[]}
            total_weight: float number
        """
        keywords = jieba.analyse.extract_tags(query, topK=self.topk, withWeight=True)
        total_weight = self.topk / sum([r[1] for r in keywords])
        return keywords,total_weight

    def text_segmentate(self, text, maxlen, seps='\n', strips=None):
        """将文本按照标点符号划分为若干个短句
        """
        text = text.strip().strip(strips)
        if seps and len(text) > maxlen:
            pieces = text.split(seps[0])
            text, texts = '', []
            for i, p in enumerate(pieces):
                if text and p and len(text) + len(p) > maxlen - 1:
                    texts.extend(self.text_segmentate(text, maxlen, seps[1:], strips))
                    text = ''
                if i + 1 == len(pieces):
                    text = text + p
                else:
                    text = text + p + seps[0]
            if text:
                texts.extend(self.text_segmentate(text, maxlen, seps[1:], strips))
            return texts
        else:
            return [text]

    def recall_title_score(self,title,keywords,total_weight):
        """计算query与标题的匹配度"""
        score = 0
        for item in keywords:
            kw, weight =  item
            if kw in title:
                score += round(weight * total_weight,4)
        return score
    
    def recall_text_score(self, text, keywords, total_weight):
        """计算query与text的匹配程度"""
        score = 0
        for item in keywords:
            kw, weight = item
            p11 = re.compile('%s' % kw)
            pr = p11.findall(text)
            # score += round(weight * total_weight, 4) * len(pr)
            score += round(weight * total_weight, 4) 
        return score
    
    def rank_text_by_keywords(self,query,data):
        """通过关键词进行召回"""
        
        #query分析
        keywords,total_weight = self.query_analyze(query)
        
        #先召回title
        title_score = {}
        for line in data:
            title = line['title']
            title_score[title] = self.recall_title_score(title,keywords,total_weight)
        title_score = sorted(title_score.items(),key=lambda x:x[1],reverse=True)
        # print(title_score)
        recall_title_list = [t[0] for t in title_score[:self.topd]]
        
        #召回sentence
        sentence_score = {}
        for line in data:
            title = line['title']
            text = line['text']
            if title  in recall_title_list:
                for ct in self.text_segmentate(text,self.maxlen, seps='\n。'):
                    ct = re.sub('\s+', ' ', ct)
                    if len(ct)>=20:
                        sentence_score[ct] = self.recall_text_score(ct,keywords,total_weight)

        sentence_score = sorted(sentence_score.items(),key=lambda x:x[1],reverse=True)
        recall_sentence_list = [s[0] for s in sentence_score[:self.topt]]
        return '\n'.join(recall_sentence_list)

    def rank_text_by_text2vec(self, query, data):
        """通过text2vec召回"""
        if not data:
            print("Warning: No data provided for ranking")
            return ""

        # 先召回title
        title_list = [query]
        for line in data:
            title = line['title']
            title_list.append(title)
        
        # 确保至少有两个标题，否则无法进行相似度计算
        if len(title_list) <= 1:
            print("Warning: Not enough titles for similarity calculation")
            return ""
            
        title_vectors = get_vector(title_list, 8)
        
        # 检查向量化是否成功
        if title_vectors.numel() == 0 or title_vectors.size(0) <= 1:
            print("Warning: Title vectorization failed or returned insufficient vectors")
            return ""
            
        title_score = get_sim(title_vectors)
        
        # 检查相似度计算是否成功
        if not title_score:
            print("Warning: Title similarity calculation failed")
            return ""
            
        title_score = dict(zip(title_score, range(1, len(title_list))))
        title_score = sorted(title_score.items(), key=lambda x:x[0], reverse=True)
        
        # 确保有足够的标题用于召回
        if not title_score or self.topd <= 0:
            print("Warning: No title scores or invalid topd parameter")
            return ""
            
        recall_title_list = [title_list[t[1]] for t in title_score[:min(self.topd, len(title_score))]]

        # 召回sentence
        sentence_list = [query]
        for line in data:
            title = line['title']
            text = line['text']
            if title in recall_title_list:
                for ct in self.text_segmentate(text, self.maxlen, seps='\n。'):
                    ct = re.sub('\s+', ' ', ct)
                    if len(ct) >= 20:
                        sentence_list.append(ct)

        # 确保至少有两个句子，否则无法进行相似度计算
        if len(sentence_list) <= 1:
            print("Warning: Not enough sentences for similarity calculation")
            return ""
            
        sentence_vectors = get_vector(sentence_list, 8)
        
        # 检查向量化是否成功
        if sentence_vectors.numel() == 0 or sentence_vectors.size(0) <= 1:
            print("Warning: Sentence vectorization failed or returned insufficient vectors")
            return ""
        
        sentence_score = get_sim(sentence_vectors)
        
        # 检查相似度计算是否成功
        if not sentence_score:
            print("Warning: Sentence similarity calculation failed")
            return ""
            
        sentence_score = dict(zip(sentence_score, range(1, len(sentence_list))))
        sentence_score = sorted(sentence_score.items(), key=lambda x:x[0], reverse=True)
        
        # 确保有足够的句子用于召回
        if not sentence_score or self.topt <= 0:
            print("Warning: No sentence scores or invalid topt parameter")
            return ""
            
        recall_sentence_list = [sentence_list[s[1]] for s in sentence_score[:min(self.topt, len(sentence_score))]]
        return '\n'.join(recall_sentence_list)


    def query_retrieve(self,query):
        #利用搜索引擎获取相关信息
        data = search_bing(query)
        #对获取的相关信息进行召回与排序，得到背景信息
        if self.recall_way == 'keyword':
            bg_text = self.rank_text_by_keywords(query,data)
        else:
            bg_text = self.rank_text_by_text2vec(query,data)
        return bg_text


cfg = Config()
trr = TextRecallRank(cfg)
q_searching = trr.query_retrieve
