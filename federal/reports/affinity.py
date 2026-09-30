import sys
import os
import re
import json
import datetime
import time
import pandas as pd
import numpy as np
from IPython.display import JSON
from mediascope_api.core import utils

from mediascope_api.core import net as mscore
from mediascope_api.mediavortex import tasks as cwt
from mediascope_api.mediavortex import catalogs as cwc

pd.set_option('display.max_columns', None)

mnet = mscore.MediascopeApiNetwork()
mtask = cwt.MediaVortexTask()
cats = cwc.MediaVortexCats()


class AffinityReport:
    """
        Класс для расчёта Affinity-Index для каналов Федерального ТВ
    """

    def __init__(self, start_date: str, end_date: str, channels_id: list):
        """
            Параметры:
            ----------
            start_date: str
                Дата начала выгрузки
            end_date: str
                Дата окончания выгрузки
            channels_id: list
                Список из каналов Федерального ТВ и их ID
        """
        self.start_date = start_date
        self.end_date = end_date
        self.channels_id = channels_id

        # Период выгрузки
        self.date_filter = [(self.start_date, self.end_date)]
        # Выгружаемые телекомпании
        self.company_filter = f'tvCompanyId IN ({", ".join(self.channels_id)})'

        self.weekday_filter = None
        self.daytype_filter = None
        self.targetdemo_filter = None
        self.program_filter = None

        self.GEN_MAPPING = {
                'All': 'Все', 
                'W': 'Ж', 
                'M': 'М'
            }

        # Группа дохода по России
        self.INC_MAPPING = {
                        'A': '1', 
                        'B': '2', 
                        'C': '3', 
                        'a': '1', 
                        'b': '2', 
                        'c': '3'
                    }
        
    

    @staticmethod
    def replace_by_dict(s: str, mapping: dict):
        """
            Вспомогательный метод для последовательной замены подстрок в строке s на основе словаря mapping
        """
        for old, new in mapping.items():
            s = s.replace(old, new)
        return s
    

    def age_to_condition(self, age_str: str):
        """
            Метод для генерации возраста ЦА в формате mediascope_api.

            Параметры:
            ----------
            age_str: str
                Возраст, заданный в формате строки

            Returns:
            ----------
                Строка с возрастом в формате mediascope_api
        """
    
        if "+" in age_str:
            start = age_str.replace("+", "").strip()
            return f"age >= {start}"
        
        else:
            start, end = age_str.split("-")
            return f"age >= {start.strip()} AND age <= {end.strip()}"
        

    def gen_to_condition(self, gen_str: str):
        """
            Метод для генерации пола в формате mediascope_api зависимости от заданного пола в формате строки.
            Если пол не "Ж" и не "М", то возвращается пустая строка.

            Параметры:
            ----------
            gen_str: str
                Пол мужчины: "м" или женщины: "ж"
            
            Returns:
            ----------
                Строка с полом в формате mediascope_api
        """
        gen_str = gen_str.lower()
        
        if gen_str == 'м': 
            return 'sex = 1'
        
        elif gen_str == 'ж': 
            return 'sex = 2'
        
        else: 
            return ''


    def inc_to_condition(self, inc_str: str = " "):
        """
            Метод для генерации Группы Дохода по России в формате mediascope_api

            Параметры:
            ----------
                inc_str: str
                    Строка с группой дохода

            Returns:
            ----------
                Строка с группой дохода в формате mediascope_api
        """
        if inc_str == " ": 
            return " "
        else:    
            inc_str = ",".join(n for n in AffinityReport.replace_by_dict(inc_str, self.INC_MAPPING))
            return f"incomeGroupRussia IN ({inc_str})"
    

    def create_targets_dict(self, target_names: list):
        """
            Метод для генерации БЦА для формирования расчета

            Параметры:
            ----------
                target_names: list
                    Список из ЦА
        """
    
        targets = {}
        for tname in target_names:
        
            conditions = []
        
            tname = tname.capitalize()
            tname = AffinityReport.replace_by_dict(tname, self.GEN_MAPPING)
            funcs = [self.gen_to_condition, self.age_to_condition, self.inc_to_condition]
            conditions = tname.split(" ")
            cond_list = [func(v) for func, v in zip(funcs, conditions)]
            cond_list = [c for c in cond_list if c != " "]
            condition = " AND ".join(cond_list)
            tname = AffinityReport.replace_by_dict(tname, {"a": "A", "b": "B", "c": "C"})
            targets[tname] = condition
        
        return targets
    

    def get_data_for_affinity_ooh(self, statistic: str, targets_dict: dict = None):
        """
            Методя для выгрузки данных для расчета Affinity с использованием базы BigTV.

            Параметры:
            ----------
                statistic: str
                    Заданная статистика
                targets_dict: dict
                    Словарь из целевых аудиторий

            Returns:
            ----------
                pd.DataFrame: таблица с выгруженными результатами из БД
        """
    
        targets = targets_dict
        locations = {'Дом, Дача, Вне Дома': 'locationId IN (1,2,4)'}
        playbacks = {'Live' : 'playBackTypeId IN (0)'}
        platforms = {'TV': 'platformId IN (1)'}
        
        if statistic not in ['StandTVR', 'SalesTVR']:
            raise ValueError(f"Неверно задана статистика. Выберите статистику из списка: ['StandTVR', 'SalesTVR']!")
    
        elif statistic == 'StandTVR':
        
            statistics = ['SpotByBreaksStandRtgPerSum']
            slices = ['tvCompanyName','researchMonth']
            combinations = utils.combine_dicts(locations, playbacks, platforms, targets)
        
            tasks = []
            print("Отправляем задания на расчет")

            for k, v in combinations.items():
    
                project_name = k.split(";")[-1].strip()
    
                location_filter = v[0]
                playbacktype_filter = v[1]
                platform_filter = v[2]
                basedemo_filter = v[3]
    
                task_json = mtask.build_crosstab_task(
                                date_filter = self.date_filter, weekday_filter = self.weekday_filter,
                                daytype_filter = self.daytype_filter, company_filter = self.company_filter,
                                location_filter = location_filter, basedemo_filter = basedemo_filter,
                                targetdemo_filter = self.targetdemo_filter, program_filter = self.program_filter,
                                break_filter = 'breaksDistributionType IN (N) AND breaksContentType IN (C)',
                                ad_filter = None, platform_filter = platform_filter,
                                playbacktype_filter = playbacktype_filter, 
                                slices = slices, statistics = statistics,
                                sortings = {'tvCompanyName': 'ASC', 'researchMonth': 'ASC'},
                                options = {
                                    "kitId": 7, #Big TV
                                    "bigTv": True,
                                    "issueType": "AD" #Тип события - Ролики
                                    }
                    )

                tsk = {}
                tsk['project_name'] = project_name    
                tsk['task'] = mtask.send_crosstab_task(task_json)
                tasks.append(tsk)
                time.sleep(2)
                print('.', end = '')
    
            print(f"\nid: {[i['task']['taskId'] for i in tasks]}") 

            print('')
            # Ждем выполнения
            print('Ждем выполнения')
            tsks = mtask.wait_task(tasks)
            print('Расчет завершен, получаем результат')

            # Получаем результат
            results = []
            print('Собираем таблицу')
            for t in tasks:
                tsk = t['task'] 
                df_result = mtask.result2table(mtask.get_result(tsk), project_name = t['project_name'])        
                results.append(df_result)
            print('.', end = '')
            df = pd.concat(results)

            # Приводим порядок столбцов в соответствие с условиями расчета
            df_stand_tvr = df[['prj_name'] + slices + statistics]
    
            return df_stand_tvr
        
    
        elif statistic == 'SalesTVR':
        
            statistics = ['SpotByBreaksStandSalesRtgPerSum']
            slices = ['tvCompanyName', 'tvCompanyId', 'researchMonth']
            combinations = utils.combine_dicts(locations, playbacks, platforms)
                                           
            tasks = []
            print("Отправляем задания на расчет")

            # Для каждой комбинации формируем задание и отправляем на расчет
            for k, v in combinations.items():
    
                # Подставляем значения в параметры
                project_name = k
    
                location_filter = v[0] # место просмотра
                playbacktype_filter = v[1] # Playback
                platform_filter = v[2] # платформа
          
                # Формируем задание для API TV Index в формате JSON
                task_json = mtask.build_crosstab_task(
                                date_filter = self.date_filter, weekday_filter = self.weekday_filter, 
                                daytype_filter = self.daytype_filter, company_filter = self.company_filter,
                                location_filter = location_filter, basedemo_filter = None,
                                targetdemo_filter = self.targetdemo_filter, program_filter = self.program_filter,
                                break_filter = 'breaksDistributionType IN (N) AND breaksContentType IN (C)',
                                ad_filter = None, platform_filter = platform_filter,
                                playbacktype_filter = playbacktype_filter, 
                                slices = slices, statistics = statistics, sortings = {'tvCompanyId':'ASC'},
                                options = {
                                    "kitId": 7, #Big TV
                                    "bigTv": True,
                                    "issueType": "AD" #Тип события - Ролики
                                }
                    )

                # Для каждого этапа цикла формируем словарь с параметрами и отправленным заданием на расчет
                tsk = {}
                tsk['project_name'] = project_name    
                tsk['task'] = mtask.send_crosstab_task(task_json)
                tasks.append(tsk)
                time.sleep(2)
                print('.', end = '')
    
            print(f"\nid: {[i['task']['taskId'] for i in tasks]}") 

            print('')
            # Ждем выполнения
            print('Ждем выполнения')
            tsks = mtask.wait_task(tasks)
            print('Расчет завершен, получаем результат')

            # Получаем результат
            results = []
            print('Собираем таблицу')
            for t in tasks:
                tsk = t['task'] 
                df_result = mtask.result2table(mtask.get_result(tsk), project_name = t['project_name'])        
                results.append(df_result)
                print('.', end = '')
            df = pd.concat(results)

            # Приводим порядок столбцов в соответствие с условиями расчета
            df_sales_tvr = df[['prj_name'] + slices + statistics]                   
            return df_sales_tvr
    

    def get_data_for_affinity(self, statistic: str, targets_dict: dict = None):
        """
            Методя для выгрузки данных для расчета Affinity с использованием базы Russia0+.

            Параметры:
            ----------
                statistic: str
                    Заданная статистика
                targets_dict: dict
                    Словарь из целевых аудиторий

            Returns:
            ----------
                pd.DataFrame: таблица с выгруженными результатами из БД
        """
        
        targets = targets_dict
        slices = ['tvCompanyName', 'researchMonth']

        if statistic not in ['StandTVR', 'SalesTVR']:
            raise ValueError(f"Неверно задана статистика. Выберите статистику из списка: ['StandTVR', 'SalesTVR']!")
    
        elif statistic == 'StandTVR':
        
            statistics = ['SpotByBreaksStandRtgPerSum']
        
            tasks = []
            print("Отправляем задания на расчет")

            for target, syntax in targets.items():   
            
                project_name = target 
                basedemo_filter = syntax
        
                task_json = mtask.build_crosstab_task(
                            date_filter = self.date_filter, weekday_filter = self.weekday_filter, 
                            daytype_filter = self.daytype_filter, company_filter = self.company_filter, 
                            location_filter = 'locationId IN (1,2)', basedemo_filter = basedemo_filter, 
                            targetdemo_filter = self.targetdemo_filter, program_filter = self.program_filter, 
                            break_filter = 'breaksDistributionType IN (N) AND breaksContentType IN (C)',
                            ad_filter = None, slices = slices, statistics = statistics,
                            sortings = {'tvCompanyName': 'ASC', 'researchMonth':'ASC'},
                            options = {
                                "kitId": 1, #TV Index Russia all
                                "issueType": "AD"
                            }
                    )

                tsk = {}
                tsk['project_name'] = project_name    
                tsk['task'] = mtask.send_crosstab_task(task_json)
                tasks.append(tsk)
                time.sleep(2)
                print('.', end = '')
    
            print(f"\nid: {[i['task']['taskId'] for i in tasks]}") 

            print('')

            print('Ждем выполнения')
            tsks = mtask.wait_task(tasks)
            print('Расчет завершен, получаем результат')

            results = []
            print('Собираем таблицу')
            for t in tasks:
                tsk = t['task'] 
                df_result = mtask.result2table(mtask.get_result(tsk), project_name = t['project_name'])        
                results.append(df_result)
                print('.', end = '')
            df = pd.concat(results)
            df = df[['prj_name'] + slices + statistics]
            return df
        
    
        elif statistic == 'SalesTVR':
        
            task_json = mtask.build_crosstab_task(
                        date_filter = self.date_filter, weekday_filter = self.weekday_filter, 
                        daytype_filter = self.daytype_filter, company_filter = self.company_filter, 
                        location_filter = 'locationId IN (1,2)', basedemo_filter = None, 
                        targetdemo_filter = self.targetdemo_filter, program_filter = self.program_filter, 
                        break_filter = 'breaksDistributionType IN (N) AND breaksContentType IN (C)',
                        ad_filter = None, slices = slices,
                        statistics = ['SpotByBreaksStandSalesRtgPerSum'], 
                        sortings = {'tvCompanyName': 'ASC', 'researchMonth':'ASC'},
                        options = {
                                "kitId": 1, #TV Index Russia all
                                "issueType": "AD"
                            }
                )
        
            sales_task = mtask.wait_task(mtask.send_crosstab_task(task_json))
            df_sales = mtask.result2table(mtask.get_result(sales_task))
            return df_sales
    

#    def append_dfs_to_excel(self, filename, df_dict):
#        """
#            Метод для записи данных в выходной файл в формате xlsx.
#        """
#        old_data = {}
#        if os.path.exists(filename):
#            old_data = pd.read_excel(filename, sheet_name = None, index_col = 0)
#        
#        for sheet_name, df in df_dict.items():
#            if sheet_name in old_data:
#                old_data[sheet_name] = pd.concat(
#                [old_data[sheet_name], df])
#            
#            else:
#                old_data[sheet_name] = df
#                
#        with pd.ExcelWriter(filename, engine = "openpyxl") as writer:
#            for sheet_name, df in old_data.items():
#                df.to_excel(writer, sheet_name = sheet_name)