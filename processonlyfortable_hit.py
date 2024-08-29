import os
import requests
from urllib.parse import urlparse
import re
import time
from typing import List, Tuple, Optional, Dict
import logging
from IPython.display import HTML, display
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# import fitz  # PyMuPDF
import shapely.geometry as sg
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity
import concurrent.futures
from GeneralAgent import Agent
from paddleocr import PaddleOCR, draw_ocr
import ollama

model = 'gpt-4o-mini'
role_prompt = """
"""
local_prompt = """
"""
rec_prompt = """
"""

def clean_string(text):
    # 移除HTML外部網站連結
    pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    text = re.sub(pattern, '', text)
    # 移除換行符號
    text = text.replace('\n', '')
    
    return text


def preprocess(
        # pymupdf_content: str,
        pymupdf_table_list: List[Dict],
        image_path: List[str],
        rects:List[List[Tuple]], #座標位置
        pdf_path:str,
        openai_api_key:str,
        output_dir:str,
        ocr_lang :str='en',
) -> str:
    result=""
    ocr = PaddleOCR(use_angle_cls=True, lang=ocr_lang)  #chinese_cht
    role_prompt = """你是一個圖片摘要生成的機器人，請你對這張圖片進行摘要，若有語言出現的話請辨別該語言並輸出成文字．
"""
    local_prompt = """你是一個圖片摘要生成的機器人，請你對這張圖片進行摘要，若有語言出現的話請辨別該語言並輸出成文字．
"""
    rec_prompt = """
"""
    image_dict = dict()
    txt_pages = list()
    if not os.path.isdir("parse_txt"):
        os.makedirs("parse_txt")
    file_name = os.path.basename(pdf_path)
    # final_output_path = "parse_txt/"+str(file_name.rstrip(".pdf"))+"_parse.txt"

    # # 確保 parse_txt 目錄存在
    # os.makedirs(os.path.dirname(final_output_path), exist_ok=True)
    # # 以寫入模式打開文件，這會清空文件（如果已存在）或創建新文件
    # with open(final_output_path, "w") as file:
    #     pass  # 不寫入任何內容，只是創建或清空文件

    # # 先把資料夾裡面過往跑過的資料清好，包含圖片和文字檔，再進行操作

    GPT_FLAG = True
    GPT_COUNT = 0
    NOTSAMELENGTH = 0
    def download_image(image_url, save_directory=output_dir):
        # Create the save directory if it doesn't exist
        os.makedirs(save_directory, exist_ok=True)
        
        # Get the filename from the URL
        filename = os.path.basename(urlparse(image_url).path)
        
        # Full path to save the image
        save_path = os.path.join(save_directory, filename)
        
        # Download the image
        response = requests.get(image_url)
        if response.status_code == 200:
            with open(save_path, 'wb') as file:
                file.write(response.content)
            print(f"Downloaded: {filename}")
        else:
            print(f"Failed to download: {filename}")
        return filename

    def find_pic_images(directory = output_dir,page_number=0):
        pic_images = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.startswith(str(page_number)+'_') and file.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif")):
                    pic_images.append(os.path.join(root, file))
        return pic_images

    def filter_table(content:str)-> str:
        table_pattern = re.compile(
        r'(\|(?:[^\n]*\|)+\n'   # 匹配表格头部行
        r'\|(?:\s*[-:]+\s*\|)+\s*\n'  # 匹配表格分隔行
        r'(?:\|(?:[^\n]*\|)\n)+)'  # 匹配表格内容行
        )
        result = table_pattern.findall(content)
        return result


    #utilize image and table 
    for index,i in enumerate(pymupdf_table_list):
        if GPT_FLAG == True:
            time.sleep(1.5)
            GPT_FLAG = False
        ocr_table_list = []
        final_content_list = []
        final_image_list = []
        table_markdown = []
        if i['tables'] !=[] and i['images']!= []: #圖片表格都存在 ex. i[image] = 4
            #比對座標來判斷是否為表格
            table_markdown = filter_table(i['text'])
            image_list = [filename for filename in image_path if filename.startswith(str(index)+'_')] # 2個
            table_list = []
            rect = rects[index]  #取出gptpdf座標
            for j in i['tables']: # i table是pymupdf的東西
                for ind,k in enumerate(rect):
                    contract = (j['bbox'][1] - k[1]) + (j['bbox'][0]-k[0])
                    #rect_h_l = (k[2]-k[0])+(k[3]-k[1])
                    #pymu_h_l = (j['bbox'][2]-j['bbox'][0])+(j['bbox'][3]-j['bbox'][1])
                    #if (abs(rect_h_l-pymu_h_l)) < 30 and (abs(k[0]-j[0]))<30:
                    #如果gptpdf抓到的圖片位置足夠接近表格的話這張圖片就是表格
                    if (abs(contract) < 40):
                        path = str(index) + '_' + str(ind) +'.png'
                        image_list.remove(path) #2
                        table_list.append(path)

            remain_list = table_list
            for inde,table_path in enumerate(table_list):


                table_role_prompt= """
                你現在是一位專注於製作HTML表格的工程師，你的任務是要畫出一個可以顯示的表格。
                """
                table_local_prompt = """
                你現在是一個製作HTML表格的工程師，你的任務是將圖片中表格的架構以及markdown的內容作結合，你必須要做到:
                1.請使用合併儲存格完整的表現出表格的結構，請勿必要遵守        
                2.你的輸出務必是完整的HTML格式，請不要省略輸出。
                3.請適度的參考Markdown的內容。
                4.請注意會有無線表格的狀況，務必使得其結構更準確。
                5. 請不要輸出除了html格式以外的任何內容，例如「以下是輸出的檔案內容」等等
                6. 使用表格內的語言回答問題。
                最後再強調一次，請確保表格的完整性與是否與圖片中的格式相符，絕對不能有跑版的狀況出現。

                """

                max_hit = []
                for index_markdown, table_text in enumerate(table_markdown):
                    comment = 0
                    ocr_result = ocr.ocr(output_dir+table_path, cls=True)
                    total = len(ocr_result[0])
                    for index_result, content in enumerate(ocr_result[0]):
                        if ocr_result[0][index_result][1][0] in table_text:
                            comment += 1
                    hit_ratio = comment / total
                    max_hit.append(hit_ratio)
                print(f"max_hit is {max_hit.index(max(max_hit))}, and its value is {max(max_hit)}")
                agent = Agent(role=table_role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                local_prompt = table_local_prompt + table_markdown[max_hit.index(max(max_hit))]
                content = agent.run([local_prompt, {'image': output_dir+table_path}])
                remain_list.remove(table_path)        
                print(f"response:{content}")
                final_content_list.append(content)
                GPT_FLAG = True
                GPT_COUNT += 1
            if remain_list != []:
                NOTSAMELENGTH += 1
                for remain in remain_list:
                    agent = Agent(role=table_role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                    local_prompt = table_local_prompt
                    content = agent.run([local_prompt, {'image': output_dir+remain}])
                    print(f"response:{content}")
                    final_content_list.append(content)

            for inde_n,n in enumerate(image_list):

                role_prompt = """你是一個圖片摘要生成的機器人，請你對這張圖片進行摘要，若有語言出現的話請辨別該語言並輸出成文字．
                """
                local_prompt = """你是一個圖片摘要生成的機器人，請你對這張圖片進行摘要，若有語言出現的話請辨別該語言並輸出成文字．
                """
                agent = Agent(role=role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                local_prompt = local_prompt
                content = agent.run([local_prompt, {'image': output_dir+n}])

                image_dict[content] = f"{output_dir}{n}"
                print(f"response:{content}")
                final_image_list.append(content)
                GPT_FLAG = True
                GPT_COUNT += 1
        elif i['tables'] == [] and i['images']!= []: #只有圖片
            #看有幾張圖片
            image_list = [filename for filename in image_path if filename.startswith(str(index)+'_')]
            for j in image_list:
                role_prompt = """你是一個圖片摘要生成的機器人，請你對這張圖片進行摘要，若有語言出現的話請辨別該語言並輸出成文字．
                """
                local_prompt = """你是一個圖片摘要生成的機器人，請你對這張圖片進行摘要，若有語言出現的話請辨別該語言並輸出成文字．
                """
               #call llm
                agent = Agent(role=role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                local_prompt = local_prompt
                content = agent.run([local_prompt, {'image':output_dir+j}])

                image_dict[content] = output_dir + j
                print(f"response:{content}")
                final_image_list.append(content)
                GPT_FLAG = True
                GPT_COUNT += 1
        elif i['tables'] != [] and i['images'] == []: #只有表格
            #看有幾個表格 
            table_role_prompt= """
            你現在是一位專注於製作HTML表格的工程師，你的任務是要畫出一個可以顯示的表格。
            """
            table_local_prompt = """
            你現在是一個製作HTML表格的工程師，你的任務是將圖片中表格的架構以及markdown的內容作結合，你必須要做到:
                            1.請使用合併儲存格完整的表現出表格的結構，請勿必要遵守               
            2.你的輸出務必是完整的HTML格式，請不要省略輸出。
            3.請適度的參考Markdown的內容。
            4.請注意會有無線表格的狀況，務必使得其結構更準確。
            5.請不要輸出除了html格式以外的任何內容，例如「以下是輸出的檔案內容」，或是「```」。
            6.請你以圖片表格中的結構為主，參考Markdown的內容。
            最後再強調一次，請確保表格的完整性與是否與圖片中的格式相符，絕對不能有跑版的狀況出現。      
            """
            table_list = [filename for filename in image_path if filename.startswith(str(index)+'_')]
            print(f"table list:{table_list}")
            table_markdown = filter_table(i['text'])
            # for j in table_markdown:  #將純文本表格去除
            #     pymupdf_content = pymupdf_content.replace(j,'')
            remain_list = table_list
            #if len(table_list) != len(table_markdown):
            for index_table, table_path in enumerate(table_list):
                max_hit = []
                for index_markdown, table_text in enumerate(table_markdown):
                    comment = 0
                    ocr_result = ocr.ocr(output_dir+table_path, cls=True)
                    total = len(ocr_result[0])
                    for index_result, content in enumerate(ocr_result[0]):
                        if ocr_result[0][index_result][1][0] in table_text:
                            comment += 1
                    hit_ratio = comment / total
                    max_hit.append(hit_ratio)
                print(f"max_hit is {max_hit.index(max(max_hit))}, and its value is {max(max_hit)}")
                agent = Agent(role=table_role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                local_prompt = table_local_prompt + table_markdown[max_hit.index(max(max_hit))]
                content = agent.run([local_prompt, {'image': output_dir+table_path}])
                remain_list.remove(table_path)        
                print(f"response:{content}")
                final_content_list.append(content)
                GPT_FLAG = True
                GPT_COUNT += 1
            if remain_list != []:
                NOTSAMELENGTH += 1
                for remain in remain_list:
                    agent = Agent(role=table_role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                    local_prompt = table_local_prompt
                    content = agent.run([local_prompt, {'image': output_dir+remain}])
                    print(f"response:{content}")
                    final_content_list.append(content)
        elif i['tables'] == [] and i['images'] == []: #代表沒有找到表格但是說不定會有擷取到表格圖片，用OCR解
            # 如果pymupdf4llm沒有找到任何東西的話，就把資訊丟到最下面當補充，ＯＣＲ不能夠確定東西在哪裡，檔案另外存
            res = find_pic_images(page_number=index)
            if res != []:
                for image_ in res:
                    ocr_result = ocr.ocr(image_, cls=True)
                    first_chunk = ocr_result[0][0][1][0] 
                    last_chunk = ocr_result[0][-1][1][0]
                    fisrt_find = pymupdf_table_list[index]['text'].find(first_chunk[:20])
                    last_find = pymupdf_table_list[index]['text'].find(last_chunk[len(last_chunk)-20:])
                    if last_find != -1 and fisrt_find != -1:  #find the text
                        table_role_prompt= """
                        你現在是一位專注於製作HTML表格的工程師，你的任務是要畫出一個可以顯示的表格。
                        """
                        table_local_prompt = """
                        你現在是一個製作HTML表格的工程師，你的任務是將圖片中表格的架構以及markdown的表格中的內容作結合，你必須要做到:
                        1.請判斷該圖片中的上下文與表格之間是否有關聯，如果無關，可以請你無視。
                        2.請專注在圖片表格的結構，完整的表現出原本的架構。
                        3.請使用Markdown中的文字來填入表格中。
                        4.請注意合併儲存格，讓結構完整。   
                        5.請你注意表格生成的合理性，並判斷表格位置是否正確。 
                        6. 請不要輸出除了html格式以外的任何內容，例如「以下是輸出的檔案內容」等等           
                        """
                        text = pymupdf_table_list[index]['text'][fisrt_find:last_find+20]
                        agent = Agent(role=table_role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                        local_prompt = table_local_prompt + text
                        content = agent.run([local_prompt, {'image': image_}])

                        GPT_FLAG = True
                        GPT_COUNT += 1
                        #這裡不能加東西這裡不能加東西這裡不能加東西
                        #因為是ＯＣＲ的所以一定要放在最下面
                        ocr_table_list.append(content)
                    else:
                        #當作圖片處理
                        role_prompt = """你現在是一位專注於製作HTML表格的工程師，你的任務是要畫出一個可以顯示的表格。
                         """
                        local_prompt = """你現在是一個製作HTML表格的工程師，你的任務是將圖片中表格的架構與文字轉成HTML表格，你必須要做到:
                        1.請判斷該圖片中的上下文與表格之間是否有關聯，如果無關，可以請你無視。
                        2.請專注在圖片表格的結構，完整的表現出原本的架構。
                        3.請專注在圖片文字，盡量預測正確。
                        4.請注意合併儲存格，讓結構完整。   
                        5.請你注意表格生成的合理性，並判斷表格位置是否正確。
                        6. 請不要輸出除了html格式以外的任何內容，例如「以下是輸出的檔案內容」等等
                        """
                        agent = Agent(role=role_prompt, api_key=openai_api_key, base_url=None, model=model, disable_python_run=False)
                        local_prompt = local_prompt
                        content = agent.run([local_prompt, {'image':image_}])

                        image_dict[content] = image_
                        GPT_FLAG = True
                        GPT_COUNT += 1
                        final_image_list.append(content)
        print(f"============== processing page {index} ==============")
        print(f"number of table: {len(final_content_list)}")
        print(f"number of image: {len(final_image_list)}")

        page_content = pymupdf_table_list[index]['text']

        #最後的輸出文字
        output_text = ""

        #若有非OCR的表格，則特別處理
        if len(final_content_list) > 0:
            # 如果長度一樣，則找找看有沒有辦法replace
            if len(final_content_list) == len(table_markdown):
                for num_table, raw_table in enumerate(table_markdown):
                    new_page_content = page_content.replace(raw_table, final_content_list[num_table])
                    if new_page_content == page_content:
                        # 如果replace没有成功，新增内容到page_content的最下面
                        page_content += final_content_list[num_table]
                    else:
                        # 如果replace成功，更新page_content
                        page_content = new_page_content
                page_content = clean_string(page_content)
                output_text += page_content
            else:
                NOTSAMELENGTH += 1
                page_content = clean_string(page_content)
                output_text += page_content
                for table_index, table in enumerate(final_content_list):
                    output_text += f"single table {table_index}:"
                    output_text += table
                    output_text += f"end of single table {table_index}"

        # 如果剛剛還沒有寫入page_content，現在寫
        if len(final_content_list) == 0:
            page_content = clean_string(page_content)
            output_text += page_content

        # 最後，如果有OCR的表格，放在page_content的最下面
        if len(ocr_table_list) > 0:
            for ocrtable_index, ocr_table in enumerate(ocr_table_list):
                output_text += f"ocr table {ocrtable_index}:"
                output_text += f"{ocr_table}"
                output_text += f"end of ocr table {ocrtable_index}:"
        
        # 最後，如果有圖片，也是放在page_content的最下面
        if len(final_image_list) > 0:
            for index_image, image_content in enumerate(final_image_list):
                output_text += f"image {index_image}:"
                output_text += f"{image_content}"  #將HTML存進去txt
                output_text += f"end of image {index_image}"

        output_dict = {
            "page": index+1,
            "text": output_text
        }
        txt_pages.append(output_dict)

        # with open(final_output_path, "a") as file:
        #     file.write(output_text)
        print(f"============== End of processing page {index} ==============")
        
    return image_dict, GPT_COUNT, NOTSAMELENGTH, txt_pages