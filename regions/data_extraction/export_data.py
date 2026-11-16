import pandas as pd
import numpy as np
import datetime as dt
import sys
from datetime import datetime
import pymorphy3 as pmrph
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
import OMA_tools
from OMA_tools.io_data.operations import File, Table, Dict_Operations
from OMA_tools.io_data.dates import Dates_Operations
from OMA_tools.io_data.colors import *
import os
import re
import json
import time
import openpyxl
import copy
from IPython.display import JSON
from mediascope_api.core import net as mscore
from mediascope_api.mediavortex import tasks as cwt
from mediascope_api.mediavortex import catalogs as cwc

import warnings
warnings.filterwarnings('ignore')

# Настраиваем отображение
pd.set_option('display.max_columns', None)

# Создаем объекты для работы с TVI API
mnet = mscore.MediascopeApiNetwork()
mtask = cwt.MediaVortexTask()
cats = cwc.MediaVortexCats()


MONTHS_RU = {
    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
}


BCA_NEED_REPLACE = {
        'All 18+': 'ВСЕ 18+',
        'All 14-59': 'ВСЕ 14-59',
        'All 14-44': 'ВСЕ 14-44',
        'All 25-54': 'ВСЕ 25-54',
        'All 6-54': 'ВСЕ 6-54',
        'All 10-45': 'ВСЕ 10-45',
        'All 14-54': 'ВСЕ 14-54',
        'All 4-45': 'ВСЕ 4-45',
        'All 25-49': 'ВСЕ 25-49',
        'W 25-59': 'Ж 25-59',
        'W 14-44': 'Ж 14-44'
}


#Класс, который мьютит все принты в консоли
class WrapperNoPrints:
    def __enter__(self):
        self._original_stdout = sys.stdout
        sys.stdout = None
    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self._original_stdout


class DataConfig:
    """
        Конфигурационные параметры
    """
    STATISTICS = ['Share']
    COMMON_TIME_FILTER = 'timeBand1 >= 50000 AND timeBand1 < 290000'
    COMMON_OPTIONS = {
        "kitId": 3,
        "totalType": "TotalChannels"
    }
    DEFAULT_SORTINGS = {'tvCompanyName': 'ASC'}

    LOCATION_FILTER = None #Если None, то Дом и Дача
    WEEKDAY_FILTER = None #Задаем дни недели
    DAYTYPE_FILTER = None #Задаем тип дня
    TARGETDEMO_FILTER = None #Дополнительный фильтр на ЦА для расчета Affinity
    ADD_CITY_TO_BASEDEMO_FROM_REGION = True
    ADD_CITY_TO_TARGETDEMO_FROM_REGION = True
    

class Builder:
    """
        Класс для генерации задач
    """
    def __init__(self):
        pass


    def preprocess_constant_files(
            self, regions_id_path: str, 
            table_sample_path: str, 
            sheet_name_1: str = 'for_comments', 
            sheet_name_2: str = 'for_team_lead'):
        """
            Метод для чтения файлов с ID регионов, которые будем выгружать, 
            а также заготовка таблицы дял генерации комментариев и для руководителей.

            Параметры:
            ----------
                regions_id_path: str
                    Путь к файлу, в котором хранятся ID регионов
                table_sample_path: str
                    Путь к файлу, в котором хранятся заготовки таблиц для генерации комментариев, а также для руководителей групп
                sheet_name_1: str
                    Название листа с заготовкой для генерации комментариев
                sheet_name_2: str
                    Название листа с заготовкой для руководителей групп

            Returns:
            ----------
                regions_dict: dict
                    Словарь, в котором ключ - название ЦА, значение - список из выгружаемых регионов для этой ЦА
                share_table: pd.DataFrame
                    Заготовка-таблица для генерации комментариев
                team_lead_table: pd.DataFrame
                    Заготовка-таблица для руководителей групп
        """
        data = pd.read_excel(regions_id_path, sheet_name = None)
        regions_dict = {bca: list(df['ID']) for bca, df in data.items()}

        # Чтение шаблона таблицы для руководителей групп и генерации комментариев
        share_table, team_lead_table = (
            pd.read_excel(table_sample_path, sheet_name = [sheet_name_1, sheet_name_2]).values()
        )

        share_table[['Channel', 'City', 'BCA']] = (
            share_table[['Channel', 'City', 'BCA']].apply(lambda s: s.str.upper())
        )

        share_table['tvCompanyName'] = share_table['Channel'] + ' (' + share_table['City'] + ')'
        team_lead_table['tvCompanyName'] = team_lead_table['Телеканал'] + ' (' + team_lead_table['Город'] + ')'

        return regions_dict, share_table, team_lead_table
    

    def build_timeband_params(
        self, date_filter, company_filter, basedemo_filter,
        time_filter, statistics, slices, sortings, options,
        location_filter, weekday_filter, daytype_filter, 
        targetdemo_filter, add_city_to_basedemo_from_region = True, 
        add_city_to_targetdemo_from_region = True
        ):
        """
            Метод дял генерации ОДНОЙ задачи в формате JSON.
        """
        # ПОЛНОЕ КОПИРОВАНИЕ ВСЕХ ПАРАМЕТРОВ
        safe_params = {
            'date_filter': copy.deepcopy(date_filter) if date_filter is not None else None,
            'company_filter': copy.deepcopy(company_filter) if company_filter is not None else None,
            'basedemo_filter': copy.deepcopy(basedemo_filter) if basedemo_filter is not None else None,
            'time_filter': copy.deepcopy(time_filter) if time_filter is not None else None,
            'statistics': copy.deepcopy(statistics) if statistics is not None else None,
            'slices': copy.deepcopy(slices) if slices is not None else None,
            'sortings': copy.deepcopy(sortings) if sortings is not None else None,
            'options': copy.deepcopy(options) if options is not None else None,
            'location_filter': copy.deepcopy(location_filter) if location_filter is not None else None,
            'weekday_filter': copy.deepcopy(weekday_filter) if weekday_filter is not None else None,
            'daytype_filter': copy.deepcopy(daytype_filter) if daytype_filter is not None else None,
            'targetdemo_filter': copy.deepcopy(targetdemo_filter) if targetdemo_filter is not None else None
        }


        # Формируем задание для API TV Index в формате JSON
        task_json = mtask.build_timeband_task(
                            date_filter = safe_params['date_filter'], 
                            weekday_filter = safe_params['weekday_filter'], 
                            daytype_filter = safe_params['daytype_filter'], 
                            company_filter = safe_params['company_filter'], 
                            time_filter = safe_params['time_filter'], 
                            basedemo_filter = safe_params['basedemo_filter'], 
                            targetdemo_filter = safe_params['targetdemo_filter'],
                            location_filter = safe_params['location_filter'], 
                            slices = safe_params['slices'], 
                            statistics = safe_params['statistics'], 
                            sortings = safe_params['sortings'], 
                            options = safe_params['options'],
                            add_city_to_basedemo_from_region = add_city_to_basedemo_from_region,
                            add_city_to_targetdemo_from_region = add_city_to_targetdemo_from_region
        )
        return task_json


    def generate_json_tasks(
        self, 
        date_filter: list, 
        bca_list: list, 
        company_dict: dict, 
        regions_dict: dict, 
        basedemo_dict: dict,
        statistics: list, 
        slices: list, 
        sortings: list,
        time_filter = DataConfig.COMMON_TIME_FILTER, 
        options =  DataConfig.COMMON_OPTIONS,
        location_filter = DataConfig.LOCATION_FILTER, 
        weekday_filter = DataConfig.WEEKDAY_FILTER, 
        daytype_filter = DataConfig.DAYTYPE_FILTER, 
        targetdemo_filter = DataConfig.TARGETDEMO_FILTER, 
        add_city_to_basedemo_from_region = True, 
        add_city_to_targetdemo_from_region = True):
        """
            Метод для генерации задач в формате JSON для ВСЕХ регионов и каналов ДЛЯ ЗАДАННОЙ СТАТИСТИКИ.

            Параметры:
            ----------
                date_filter: list
                    Временной период для выгрузки данных, заданный в виде списка ('Начало', 'Конец') в формате 'YYYY-MM-DD'.
                bca_list: list
                    Список из выгружаемых ЦА.
                company_dict: dict
                    Словарь из телекомпаний, где ключ - название ЦА, значение - выгружаемые телекомпании для данной ЦА в формате str.
                regions_dict: dict
                    Словарь из регионов, где ключ - название ЦА, значение - список из номеров выгружаемых регионов.
                basedemo_dict: dict
                    Словарь из целых аудиторий, где ключ - название ЦА, значение - название ЦА в формате mediasope_api "age >= 18".
                statistics: list
                    Список строк из выгружаемых статистик.
                slices: list
                    Список строк из срезов.
                sortings: list
                    Список строк с условиями сортировки по срезам.
                time_filter: str
                    Временной интервал вещания.
                options: dict
                    Словарь, в котором указана БД, из которой будем делать выгрузку.
                location_filter: str
                    Место просмотра.
                weekday_filter: str
                    Дни недели.
                daytype_filter: str
                    Тип дня.
                targetdemo_filter: str
                    Доп фильтр ЦА, нужен только в случае расчета отношения между ЦА, например, при расчете Affinity Index.
                add_city_to_basedemo_from_region: bool
                    Вспомогательный параметр. Если True - выгрузка из региональной базы, в противном случае нет. По умолчанию False.
                add_city_to_targetdemo_from_region: bool
                    Вспомогательный параметр. Если True - выгрузка из региональной базы, в противном случае нет. По умолчанию False.

            Returns:
            ----------
                json_tasks_full: dict
                    Словарь с сформированными задачами в формате JSON для последующей отправки на сервер.
        """
        import time

        start = time.perf_counter()

        print(Color.BOLD + Color.BLUE + '=== 🕑 ФОРМИРУЮ ЗАДАЧИ В ФОРМАТЕ JSON ДЛЯ ОТПРАВКИ НА СЕРВЕР ===' + Color.END)
        print('Пожалуйста, подождите, этот процесс обычно занимает около  5 секунд.')

        json_tasks_full = {}
        for bca in bca_list:
            # ШАГ 1. Отбор телекомпаний для конкретной ЦА
            companies = company_dict[bca]

            # ШАГ 2. Составление входного параметра "company_filter" на основании телекомпаний и регионов для данной ЦА
            company_filter = companies + f" AND regionId IN ({', '.join(map(str, regions_dict[bca]))})"

            # ШАГ 3. Составление входного параметра "basedemo_filter" на основании данной ЦА
            basedemo_filter = basedemo_dict[bca]

            # ШАГ 4. Формируем задание для API TV Index в формате JSON
            task_json = self.build_timeband_params(date_filter = date_filter, weekday_filter = weekday_filter, 
                daytype_filter = daytype_filter, company_filter = company_filter, 
                time_filter = time_filter, basedemo_filter = basedemo_filter, 
                targetdemo_filter = targetdemo_filter,location_filter = location_filter, 
                slices = slices, statistics = statistics, sortings = sortings, options = options,
                add_city_to_basedemo_from_region = add_city_to_basedemo_from_region, 
                add_city_to_targetdemo_from_region = add_city_to_targetdemo_from_region
            )
            #task_json = mtask.build_timeband_task(
            #    date_filter = date_filter, weekday_filter = weekday_filter, 
            #    daytype_filter = daytype_filter, company_filter = company_filter, 
            #    time_filter = time_filter, basedemo_filter = basedemo_filter, 
            #    targetdemo_filter = targetdemo_filter,location_filter = location_filter, 
            #    slices = slices, statistics = statistics, sortings = sortings, options = options,
            #    add_city_to_basedemo_from_region = True, add_city_to_targetdemo_from_region = True
            #)

            json_tasks_full[bca] = task_json
        
        end = time.perf_counter()

        print(Color.BOLD + Color.GREEN + '⭐ СФОРМИРОВАЛ ЗАДАЧИ!' + Color.END)
        print(f'Время формирования задач по факту вышло: {((end - start)):0.2f} сек.' + '\n')
        
        return json_tasks_full
    

    def execute_timeband_task(self, task_json: dict) -> pd.DataFrame:
        """
            Метод для выгрузки данных для какого-то ОДНОГО TimeBand - задания в формате JSON.

            Параметры:
            ----------
                task_json: dict
                    Словарь со сгенерированной задачей в формате JSON.
            
            Returns:
            ----------
                df: pd.DataFrame
                    Таблица с выгруженными данными
        """
        # ШАГ 1. Отправляем задание на расчет и ждем выполнения
        # Ожидание выполнения задачи
        task_timeband = mtask.wait_task(mtask.send_timeband_task(task_json))
    
        # Получаем результат
        df = mtask.result2table(mtask.get_result(task_timeband))
        return df
    

    def update_table(self, historical_data: pd.DataFrame, new_data: pd.DataFrame, column_date_name: str):
        """
            Метод для обновления файла с фактическими данными.

            Параметры:
            ----------
                historical_data: pd.DataFrame
                    Таблица со старыми данными
                new_data: pd.DataFrame
                    Таблица с новыми данными
                column_date_name: str
                    Название колонки с датой
        """

       
        new = pd.DataFrame()

        # Приведение даты к формату строки
        historical_data[column_date_name] = historical_data[column_date_name].dt.strftime('%Y-%m-%d')
        new_data[column_date_name] = new_data[column_date_name].dt.strftime('%Y-%m-%d')

        # Отбираем уникальные даты из старых и новых данных
        old_unique_dates = historical_data[column_date_name].unique()
        new_unique_dates = new_data[column_date_name].unique()

        old_ones = []

        # Фильтруем даты, которые уже присутствуют в данных
        for new_date in new_unique_dates:
                
            if new_date in old_unique_dates:
                old_ones.append(pd.to_datetime(new_date))

        if len(old_ones) != 0:
            min_date_str = min(old_ones).strftime('%Y-%m-%d')

            # Оставляем только те даты, которые не встречаются в новых, если таковые нашлись
            filtered = historical_data[historical_data[column_date_name] < min_date_str]

            if len(filtered) != 0:

                # Обновляем таблицу с фактическими данными
                new = pd.concat([filtered, new_data]).reset_index(drop = True)

        # В противном случае просто добавляем новые данные в конец старой таблицы
        else:
            new = pd.concat([historical_data, new_data]).reset_index(drop = True)

        sorted_by_dates = new.sort_values(column_date_name).reset_index(drop = True)

        return sorted_by_dates
    

    #def make_style_of_table(
    #    self,
    #    writer: pd.ExcelWriter,
    #    df: pd.DataFrame,
    #    sheet_name: str,
    #    column_configs: list,
    #    date_columns: list | None = None,
    #    use_filters: bool = True,
    #    freeze_panes: bool = True,
    #    ):
    #    """
    #        Метод для записи одного DataFrame на лист с форматированием.
#
    #        Параметры:
    #        ----------
    #            writer: pd.ExcelWriter
    #                уже открытый pd.ExcelWriter(engine = 'xlsxwriter').
    #            df: pd.DataFrame
    #                Датафрейм, который будем записывать в файл.
    #            sheet_name: str
    #                Название листа в Excel, на который будут записываться данные.
    #            column_configs: list
    #                column_configs: [{'header': 'Дата', 'width': 13.0, 'format': 'date'}, ...]
    #                Условия форматирования таблицы
    #    """
    #    if date_columns is None:
    #        date_columns = ['date', 'Дата']
#
    #    workbook = writer.book
    #    worksheet = workbook.add_worksheet(sheet_name)
    #    writer.sheets[sheet_name] = worksheet
#
    #    # --- Форматы ---
    #    header_format = workbook.add_format({
    #        'bold': True, 'text_wrap': True,
    #        'align': 'center', 'valign': 'vcenter',
    #        'bg_color': '#D9E1F2'
    #    })
    #    table_fmt = workbook.add_format({
    #        'align': 'center', 'valign': 'vcenter',
    #        'num_format': '0.00000'
    #    })
    #    date_fmt = workbook.add_format({
    #        'num_format': 'yyyy-mm-dd',
    #        'align': 'center', 'valign': 'vcenter'
    #    })
#
    #    # --- Заголовки ---
    #    headers = [cfg['header'] for cfg in column_configs]
    #    for col_num, header in enumerate(headers):
    #        worksheet.write(0, col_num, header, header_format)
#
    #    # --- Данные ---
    #    # Определяем, какие колонки — даты (по имени колонки в df)
    #    date_col_idx = {
    #        i for i, c in enumerate(df.columns)
    #        if c in date_columns or pd.api.types.is_datetime64_any_dtype(df[c])
    #    }
#
    #    for row_idx in range(len(df)):
    #        for col_idx in range(len(df.columns)):
    #            value = df.iat[row_idx, col_idx]
    #            fmt = date_fmt if col_idx in date_col_idx else table_fmt
    #            # datetime пишем как есть — xlsxwriter + num_format превратит в дату
    #            worksheet.write(row_idx + 1, col_idx, value, fmt)
#
    #    # --- Ширина колонок ---
    #    for col_idx, cfg in enumerate(column_configs):
    #        worksheet.set_column(col_idx, col_idx, cfg.get('width', 12.0))
#
    #    # --- Автофильтр ---
    #    if use_filters and len(df) > 0:
    #        last_row = len(df)            # +1 из-за заголовка → последняя строка данных
    #        last_col = len(headers) - 1
    #        worksheet.autofilter(
    #            0, 0, last_row, last_col
    #        )
#
    #    # --- Закрепление шапки и первого столбца ---
    #    if freeze_panes:
    #        worksheet.freeze_panes(1, 1)
    #
#
    #def save_tables_to_excel(
    #        self, 
    #        tables: dict[str, pd.DataFrame], 
    #        filepath: str, 
    #        column_date_name: str
    #    ):
    #    """
    #        Метод для сохранения словаря датафреймов в выходной файл с форматированием.
#
    #        Параметры:
    #        ----------
    #            tables: dict[str, pd.DataFrame]
    #                Словарь датафреймов. Каждый df: колонка с датой + колонки-каналы.
    #            filepath: str
    #                Путь к файлу, в который будут сохраняться данные.
    #            column_date_name: str
    #                Название колонки с датой.
    #    """
    #    try:
    #        with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
    #            for sheet_name, df in tables.items():
    #                # 1) Конфиг колонок строим из df
    #                column_configs = []
    #                for col in df.columns:
    #                    if col == column_date_name or pd.api.types.is_datetime64_any_dtype(df[col]):
    #                        column_configs.append({
    #                            'header': col, 'width': 13.0, 'format': 'date',
    #                        })
    #                    else:
    #                        column_configs.append({
    #                            'header': col, 'width': 20.0, 'format': 'general',
    #                        })
#
    #                # 3) Пишем
    #                self.make_style_of_table(
    #                    writer = writer,
    #                    df = df,
    #                    sheet_name = sheet_name,
    #                    column_configs = column_configs,
    #                    date_columns = [column_date_name]
    #                )
    #    except Exception as e:
    #        print(f"❌ Не удалось сохранить файл {filepath}: {e}")
    #        raise                      
    #    else:
    #        print(f"✅ Файл сохранён: {filepath} (листов: {len(tables)})")



    def make_style_of_table(
        self,
        writer: pd.ExcelWriter,
        df: pd.DataFrame,
        sheet_name: str,
        column_configs: list,
        date_columns: list | None = None,
        use_filters: bool = True,
        freeze_panes: bool = True,
        ):
        """
            Метод для записи одного DataFrame на лист с форматированием.

            Параметры:
            ----------
                writer: pd.ExcelWriter
                    уже открытый pd.ExcelWriter(engine='xlsxwriter').
                df: pd.DataFrame
                    Датафрейм, который будем записывать в файл.
                sheet_name: str
                    Название листа в Excel.
                column_configs: list
                    [{'header': 'Дата', 'width': 13.0, 'format': 'date'}, ...]
                    'format': 'text' | 'number' | 'date' | 'time' | None.
                    Если 'format' задан явно — используется он (приоритет).
                    Если None — формат определяется автоматически:
                        * колонка в date_columns / datetime-типа → date_fmt
                        * числовой dtype (int/float)               → number_fmt
                        * всё остальное (object, str, bool)        → text_fmt
                date_columns: list | None
                    Названия колонок, которые нужно трактовать как даты.
                    Если None — берутся ['date', 'Дата'].
                    Если в df таких колонок нет — это НЕ ошибка, просто
                    автоопределение дат не сработает.
                use_filters: bool
                    Включить автофильтр по шапке.
                freeze_panes: bool
                    Закрепить шапку и первый столбец.
        """
        if date_columns is None:
            date_columns = ['date', 'Дата']

        workbook = writer.book
        worksheet = workbook.add_worksheet(sheet_name)
        writer.sheets[sheet_name] = worksheet

        # --- Форматы ---
        header_format = workbook.add_format({
            'bold': True, 'text_wrap': True,
            'align': 'center', 'valign': 'vcenter',
            'bg_color': '#D9E1F2'
        })
        text_fmt = workbook.add_format({
            'align': 'center', 'valign': 'vcenter'
        })
        number_fmt = workbook.add_format({
            'align': 'center', 'valign': 'vcenter',
            'num_format': '0.00000'
        })
        date_fmt = workbook.add_format({
            'num_format': 'yyyy-mm-dd',
            'align': 'center', 'valign': 'vcenter'
        })
        time_fmt = workbook.add_format({
            'num_format': 'hh:mm:ss',
            'align': 'center', 'valign': 'vcenter'
        })

        format_map = {
            'text':   text_fmt,
            'number': number_fmt,
            'date':   date_fmt,
            'time':   time_fmt,
            'general': workbook.add_format({'align': 'center', 'valign': 'vcenter'}),
            None:     None,
        }

        # --- Заголовки ---
        headers = [cfg['header'] for cfg in column_configs]
        for col_num, header in enumerate(headers):
            worksheet.write(0, col_num, header, header_format)

        # --- Определяем формат для каждой колонки ---
        def _resolve_format(col_idx: int, cfg: dict):
            # 1) Явно заданный формат — приоритет
            fmt_name = cfg.get('format')
            if fmt_name is not None:
                return format_map[fmt_name]

            # 2) Автоопределение по dtype/имени колонки
            if col_idx >= len(df.columns):
                return text_fmt

            col_name = df.columns[col_idx]
            series = df[col_name]

            # дата: по имени ИЛИ по типу
            if col_name in date_columns or pd.api.types.is_datetime64_any_dtype(series):
                return date_fmt

            # число
            if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
                return number_fmt

            # всё остальное (object / str / bool / category) — текст
            return text_fmt

        col_formats = [
            _resolve_format(i, cfg) for i, cfg in enumerate(column_configs)
        ]

        # --- Данные ---
        for row_idx in range(len(df)):
            for col_idx in range(len(df.columns)):
                value = df.iat[row_idx, col_idx]
                fmt = col_formats[col_idx] if col_idx < len(col_formats) else None
                worksheet.write(row_idx + 1, col_idx, value, fmt)

        # --- Ширина колонок ---
        for col_idx, cfg in enumerate(column_configs):
            worksheet.set_column(col_idx, col_idx, cfg.get('width', 12.0))

        # --- Автофильтр ---
        if use_filters and len(df) > 0:
            worksheet.autofilter(0, 0, len(df), len(headers) - 1)

        # --- Закрепление шапки и первого столбца ---
        if freeze_panes:
            worksheet.freeze_panes(1, 1)
    

    def save_tables_to_excel(
        self,
        tables: dict[str, pd.DataFrame],
        filepath: str,
        column_date_name: str | None = None,   # ← сделали опциональным
        ):
        """
            Метод для сохранения словаря датафреймов в выходной файл с форматированием.

            Параметры:
            ----------
                tables: dict[str, pd.DataFrame]
                    Словарь датафреймов.
                filepath: str
                    Путь к файлу.
                column_date_name: str | None
                    Название колонки с датой. Если None — автоопределение
                    по dtype (datetime64). Полезно для таблиц без даты.
        """
        try:
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                for sheet_name, df in tables.items():
                    column_configs = []
                    for col in df.columns:
                        is_date = (
                            (column_date_name is not None and col == column_date_name)
                            or pd.api.types.is_datetime64_any_dtype(df[col])
                        )
                        is_time = pd.api.types.is_timedelta64_dtype(df[col])

                        if is_date:
                            column_configs.append({
                                'header': col, 'width': 13.0, 'format': 'date',
                            })
                        elif is_time:
                            column_configs.append({
                                'header': col, 'width': 10.0, 'format': 'time',
                            })
                        else:
                            column_configs.append({
                                'header': col, 'width': 20.0, 'format': 'general',
                            })

                    # date_columns: только если реально передали имя даты
                    date_cols = [column_date_name] if column_date_name else []

                    self.make_style_of_table(
                        writer=writer,
                        df=df,
                        sheet_name=sheet_name,
                        column_configs=column_configs,
                        date_columns=date_cols,
                    )
        except Exception as e:
            print(f"❌ Не удалось сохранить файл {filepath}: {e}")
            raise
        else:
            print(f"✅ Файл сохранён: {filepath} (листов: {len(tables)})")
            


class ExportService:
    """
        Класс для выгрузки данных
    """
    def __init__(self, date_filter):
        self.date_filter = date_filter
    

    def export_period(
            self, 
            task_json: dict,
            statistics = DataConfig.STATISTICS,
            slices = [
                'tvCompanyName',     # телекомпания
                'regionName'         # регион
            ]) -> pd.DataFrame:
        """
            Метод для выгрузки данных по дням для ОДНОЙ ЦА.

            Параметры:
            ----------
                task_json: dict
                    Словарь со сгенерированной задачей в формате JSON.
                statistics: list of str
                    Список строк из выгружаемых статистик
                slices: list of str
                    Список строк из срезов
            
            Returns:
            ----------
                df: pd.DataFrame
                    Таблица с выгруженными данными с определенными срезами.
        """
        df = Builder().execute_timeband_task(task_json)    

        # Приводим порядок столбцов в соответствие с условиями расчета
        df = df[slices + statistics]
        #order = ['tvCompanyName', 'regionName']
        #df = df[slices + statistics]
        # Удаляем строки с подстрокой "ДОП" в выгруженном датафрейме
        mask = df.apply(lambda row: row.astype(str).str.contains(r'/ ДОП', regex = True).any(), axis = 1)
        df = df[~mask]
        return df.reset_index(drop = True)
        
    

    def export_by_days(
            self, 
            task_json: dict,
            statistics = DataConfig.STATISTICS,
            slices = [
                'regionName',     # регион
                'tvCompanyName',  # телекомпания
                'researchDate'    # дата
            ]) -> pd.DataFrame:
        """
            Метод для выгрузки данных по дням для ОДНОЙ ЦА.

            Параметры:
            ----------
                task_json: dict
                    Словарь со сгенерированной задачей в формате JSON.
                statistics: list of str
                    Список строк из выгружаемых статистик
                slices: list of str
                    Список строк из срезов
            
            Returns:
            ----------
                df: pd.DataFrame
                    Таблица с выгруженными данными с определенными срезами.
        """
        df = Builder().execute_timeband_task(task_json)    

        # Приводим порядок столбцов в соответствие с условиями расчета
        df = df[slices + statistics]
        order = ['researchDate', 'tvCompanyName', 'regionName']
        df = df[order + statistics]
        df.rename(columns = {'researchDate': 'Date'}, inplace = True)
        # Удаляем строки с подстрокой "ДОП" в выгруженном датафрейме
        mask = df.apply(lambda row: row.astype(str).str.contains(r'/ ДОП', regex = True).any(), axis = 1)
        df = df[~mask]
        return df.reset_index(drop = True)


    def export_one(self, bca, task_json, export_func, **kwargs):
        df = export_func(task_json, **kwargs)
        df['ЦА'] = bca
        return bca, df
    

    def sum_2_channels(
            self,
            all_18_df: pd.DataFrame, 
            all_14_44_df: pd.DataFrame, 
            columns_to_merge: list, 
            date_column: str = None,
            statistic: str = 'Share',
            include_time_band: bool = False
        ):
        """
            Метод для суммирования показателей по каналам "РОССИЯ 1" + "ГТРК", "ПЯТНИЦА (ЕКАТЕРИНБУРГ)" + "ЧЕТВЕРТЫЙ КАНАЛ (ЕКАТЕРИНБУРГ)".
            Итоговый показатель для канала 
                "РОССИЯ 1" = "РОССИЯ 1" + "ГТРК"
                "ПЯТНИЦА (ЕКАТЕРИНБУРГ)" = "ПЯТНИЦА (ЕКАТЕРИНБУРГ)" + "ЧЕТВЕРТЫЙ КАНАЛ (ЕКАТЕРИНБУРГ)"

            Параметры:
            ----------
                all_18_df: pd.DataFrame
                    Таблица с исходными данными по аудитории "Все 18+".
                all_14_44_df: pd.DataFrame
                    Таблица с исходными данными по аудитории "Все 14-44".
                columns_to_merge: list of str
                    Список колонок, по которым будем делать merge
                date_column: str
                    Название колонки с Датой или Периодом выгрузки
                statistic: str
                    Название статистики, для которой будут производиться преобразования. По умолчанию это "Share".
                include_time_band: bool
                    Если True — в итоговых таблицах сохраняется колонка 'timeBand60'.
                    Если False — колонка 'timeBand60' исключается.
                    По умолчанию False.
            
            Returns:
            ----------
                all_18_df_new: pd.DataFrame
                    Преобразованная таблица для аудитории "Все 18+".
                all_14_44_df_new: pd.DataFrame
                    Преобразованная таблица для аудитории "Все 14-44".

        """
        # Формируем список итоговых колонок в зависимости от параметров
        if date_column is not None:
            output_cols = [f'{date_column}', 'tvCompanyName_x', 'regionName']
        else:
            output_cols = ['tvCompanyName_x', 'regionName']
        
        if include_time_band:
            output_cols.append('timeBand60')

        output_cols.extend([f'{statistic}', 'ЦА'])

        # ШАГ 1. ПРЕОБРАЗОВАНИЯ С АУДИТОРИЕЙ "ВСЕ 18+"
        tv = all_18_df['tvCompanyName'].astype(str)

        # Маски
        mask_gtrk     = tv.str.contains('ГТРК',      regex = False, na = False)
        mask_russia_1 = tv.str.contains('РОССИЯ 1',  regex = False, na = False)

        # ГТРК без РОССИЯ 24
        gtrk = all_18_df[mask_gtrk & ~tv.str.contains('РОССИЯ 24', regex = False, na = False)].reset_index(drop = True)

        # РОССИЯ 1 и остаток
        russia_1 = all_18_df[mask_russia_1].reset_index(drop = True)
        all_18_df = all_18_df[~(mask_gtrk | mask_russia_1)].reset_index(drop = True)

        # Суммирование показателей по Россия 1 и каналам ГТРК
        if len(russia_1) != len(gtrk):
            raise ValueError("Количество каналов ГТРК не соответствует количеству каналов РОССИЯ 1!")

        russia_1_new = pd.merge(russia_1, gtrk, on = columns_to_merge, how = 'inner')
        russia_1_new[f'{statistic}'] = russia_1_new[f'{statistic}_x'] + russia_1_new[f'{statistic}_y']

        russia_1_new = russia_1_new[output_cols]
        #if date_column is not None:
        #    russia_1_new = russia_1_new[[f'{date_column}', 'tvCompanyName_x', 'regionName', f'{statistic}', 'ЦА']]
        #else:
        #    russia_1_new = russia_1_new[['tvCompanyName_x', 'regionName', f'{statistic}', 'ЦА']]
        russia_1_new.rename(columns = {'tvCompanyName_x': 'tvCompanyName'}, inplace = True)

        all_18_df_new = pd.concat([all_18_df, russia_1_new]).reset_index(drop  = True)

        # ШАГ 2. ПРЕОБРАЗОВАНИЯ С АУДИТОРИЕЙ "ВСЕ 14-44"
        tv_14_44 = all_14_44_df['tvCompanyName'].astype(str)

        # Маски
        mask_4_channel = tv_14_44.str.contains('ЧЕТВЕРТЫЙ КАНАЛ (ЕКАТЕРИНБУРГ)', regex = False, na = False)
        mask_pyatniza  = tv_14_44.str.contains('ПЯТНИЦА (ЕКАТЕРИНБУРГ)',         regex = False, na = False)

        # Отбор "ПЯТНИЦА (ЕКАТЕРИНБУРГ)"
        pyatniza = all_14_44_df[mask_pyatniza].reset_index(drop = True)

        # Отбор "ЧЕТВЕРТЫЙ КАНАЛ (ЕКАТЕРИНБУРГ)"
        channel_4 = all_14_44_df[mask_4_channel].reset_index(drop = True)

        # Удаление каналов "ПЯТНИЦА (ЕКАТЕРИНБУРГ)" и "ЧЕТВЕРТЫЙ КАНАЛ (ЕКАТЕРИНБУРГ)" из общей массы
        all_14_44 = all_14_44_df[~(mask_4_channel | mask_pyatniza)].reset_index(drop = True)

        # Суммирование показателей по Россия 1 и каналам ГТРК
        if len(pyatniza) != len(channel_4):
            raise ValueError("Количество каналов ПЯТНИЦА(ЕКАТЕРИНБУРГ) не соответствует количеству каналов ЧЕТВЕРТЫЙ КАНАЛ (ЕКАТЕРИНБУРГ)!")

        pyatniza_new = pd.merge(pyatniza, channel_4, on = columns_to_merge, how = 'inner')
        pyatniza_new[f'{statistic}'] = pyatniza_new[f'{statistic}_x'] + pyatniza_new[f'{statistic}_y']

        pyatniza_new = pyatniza_new[output_cols]
        
        #if date_column is not None:
        #    pyatniza_new = pyatniza_new[[f'{date_column}', 'tvCompanyName_x', 'regionName', f'{statistic}', 'ЦА']]
        #else:
        #    pyatniza_new = pyatniza_new[['tvCompanyName_x', 'regionName', f'{statistic}', 'ЦА']]

        pyatniza_new.rename(columns = {'tvCompanyName_x': 'tvCompanyName'}, inplace = True)

        all_14_44_df_new = pd.concat([all_14_44, pyatniza_new]).reset_index(drop  = True)

        return all_18_df_new, all_14_44_df_new

    

    def make_calculation_by_days(
            self, 
            tasks_json_full: dict,
            columns_order_path: str,
            historical_filepath: str,
            filepath_last_n_days: str,
            last_n_days: int = 28,
            date_column: str = 'Date',
            save_to_output_file: bool = True
        ):
        """
            Метод для расчёта ВСЕХ задач для ВСЕХ ЦА.

            Параметры:
            ----------
                tasks_json_full: dict
                    Словарь со сгенерированными задачами в формате JSON для ВСЕХ ЦА И РЕГИОНОВ.
                columns_order_path: str
                    Путь к файлу с порядком колонок.
                historical_filepath: str
                    Путь к файлу с историческими данными.
                filepath_last_n_days: str
                    Путь к файлу с данными по последним N дням.
                last_n_days: int
                    Количество сохраняемых дней в выходной файл. По умолчанию равен 28.
                date_column: str
                    Название колонки с Датой. По умолчанию "Date".
                save_to_output_file: bool
                    Булева переменная. Если True, то производится сохранение в выходной файл. В противном случае нет. По умолчанию "True".

            Returns:
            ----------
                results: dict of pd.DataFrame
                    Словарь, где ключ - БЦА, значение - таблица со свежевыгруженными данными.
        """
        # ШАГ 1. Загрузка порядка колонок
        columns_order = Dict_Operations.load_pkl_file(columns_order_path)

        # ШАГ 2. Выгрузка данных из БД Medisacope
        import time
        start = time.perf_counter()


        results = {}
        export = partial(
            self.export_one,
            export_func = self.export_by_days,
            statistics = ['Share', 'TTVRtgPer'],        # <-- единственное изменение
        )

        with ThreadPoolExecutor(max_workers = 8) as executor:
            futures = {
                executor.submit(export, bca, task_json): bca
                for bca, task_json in tasks_json_full.items()
            }
            for future in as_completed(futures):
                bca, df = future.result()
                results[bca] = df
        
        results_analysis = {}
        for bca, df in results.items():
            results_analysis[bca] = df.drop('TTVRtgPer', axis = 1)
        
        end = time.perf_counter()
        print('\n')
        print(Color.BOLD + Color.GREEN + '=== ДАННЫЕ ВЫГРУЖЕНЫ. ДЕЛАЮ ПРЕОБРАЗОВАНИЯ. ===' + Color.END)
        print(f'Время расчета задач по факту вышло: {((end - start) / 60.):0.2f} мин.')

        ################################### МАНИПУЛЯЦИИ С "РОССИЯ 1" И КАНАЛОВ "ГТРК" ДЛЯ АУДИТОРИИ ВСЕ 18+ ###################################
        all_18 = results_analysis['All 18+'].copy()
        all_14_44 = results_analysis['All 14-44'].copy()
        all_18_new, all_14_44_new = self.sum_2_channels(
                                                all_18_df = all_18, 
                                                all_14_44_df = all_14_44, 
                                                columns_to_merge = ['Date', 'regionName', 'ЦА'], 
                                                date_column = 'Date'
                                    )
        # Новый датафрейм для аудитории "Все 18+". Перезаписываем его в словарь
        results_analysis['All 18+'] = all_18_new
        # Новый датафрейм для аудитории "Все 14-44". Перезаписываем его в словарь
        results_analysis['All 14-44'] = all_14_44_new

        # ШАГ. 3. Финальное преобразование таблиц
        for bca, df in results_analysis.items():
            results_analysis[bca] = df.pivot(index = 'Date', columns = 'tvCompanyName', values = 'Share').reset_index()
        
        # ШАГ. 4. Выстраивание нужного порядка колонок
        results_analysis = {
            bca: results_analysis[bca][cols]
            for bca, cols in columns_order.items()
            if bca in results_analysis
        }

        if save_to_output_file:
            print(Color.BOLD + Color.VIOLET + "=== СОХРАНЯЮ ВЫГРУЖЕННЫЕ ДАННЫЕ В ВЫХОДНЫЕ ФАЙЛЫ ===" + Color.END)

            # ШАГ. 5. Обновление файла с историческими данными
            share_old = pd.read_excel(historical_filepath, sheet_name = None)

            # Обновление таблиц
            updated_dict = {}
            for bca, old_df in share_old.items():
                new_data = results_analysis[bca]
                
                if len(new_data) != 0:
                    old_df['Date'] = pd.to_datetime(old_df['Date'])
                    new_data['Date'] = pd.to_datetime(new_data['Date'])
                    updated_dict[bca] = Builder().update_table(old_df, new_data, 'Date')

            # Сохранение в файл обновленных данных
            Builder().save_tables_to_excel(updated_dict, historical_filepath, 'Date')

            # ШАГ 6. Отрезание последних 28 дней из выгрузки
            df_dict_tail = {}
            for bca, df in results_analysis.items():
                if len(results_analysis[bca]) < last_n_days:
                    raise ValueError(
                        Color.BOLD + Color.RED + \
                        '❌ Количество выгружаемых дней не соответствует количеству дней, записываемых в файл. Выберите другой временной период'  + \
                        Color.END
                    )
                df_dict_tail[bca] = df.tail(last_n_days).reset_index(drop = True) #Записывает последние 28 значений из выгрузки
            
            # ШАГ 8. Запись в файл последних N дней.
            File(filename = filepath_last_n_days).to_file(df_dict_tail)

            try:
                # Установка внешнего вида итоговой таблицы по дням за последние 28 дней
                writer = pd.ExcelWriter(filepath_last_n_days, engine = 'xlsxwriter')
                for key, df in df_dict_tail.items():
                    Table(df = df).make_style_of_table(writer = writer, sheet_name = key, width_col_1 = 4.5, width_col_2 = 17.57, width_col_3 = 15.86)
                writer.close()
                print("✅ Файл с выгрузкой по дням успешно сохранен")
            except Exception as e:
                print(Color.BOLD + Color.RED + f"❌ Ошибка при сохранении: {e}" + Color.END)

        return results, results_analysis
    

    def make_api_calculation_period(
            self, 
            tasks_json_full: dict, 
            columns_order_path: str, 
            historical_filepath: str, 
            save_to_output_file: bool = True
        ):
        """
            Метод для выгрузки данных ПО ВСЕМ КАНАЛАМ-ГОРОДАМ за определенный период.

            Параметры:
            ----------
                tasks_json_full: dict:
                    Словарь из сформированных задач в формате JSON.
                columns_order_path: str
                    Путь к файлу с нужным порядком колонок
                historical_filepath: str
                    Путь к файлу с историческими данными.
                save_to_output_file: bool
                    Булева переменная. Если True, то производится сохранение в выходной файл. В противном случае нет. По умолчанию "True".
        """
        target_date = pd.to_datetime(self.date_filter[0][0])
        target_date_str = f"{MONTHS_RU[target_date.month]} {target_date.year}"

        # ШАГ 1. Загрузка порядка колонок
        columns_order = Dict_Operations.load_pkl_file(columns_order_path)

        # ШАГ 2. Выгрузка данных через API
        import time
        start = time.perf_counter()

        results = {}
        export = partial(
            self.export_one, export_func = self.export_period, statistics = ['Share']
        )

        with ThreadPoolExecutor(max_workers = 8) as executor:
            futures = {
                executor.submit(export, bca, task_json): bca
                for bca, task_json in tasks_json_full.items()
            }
            for future in as_completed(futures):
                bca, df = future.result()
                results[bca] = df
        
        end = time.perf_counter()
        print('\n')
        print(Color.BOLD + Color.GREEN + '=== ДАННЫЕ ВЫГРУЖЕНЫ. ДЕЛАЮ ПРЕОБРАЗОВАНИЯ. ===' + Color.END)
        print(f'Время расчета задач по факту вышло: {((end - start) / 60.):0.2f} мин.')

        ################################### МАНИПУЛЯЦИИ С "РОССИЯ 1" И КАНАЛОВ "ГТРК" ДЛЯ АУДИТОРИИ ВСЕ 18+ ###################################
        all_18 = results['All 18+'].copy()
        all_14_44 = results['All 14-44'].copy()
        all_18_new, all_14_44_new = self.sum_2_channels(
                                                all_18_df = all_18, 
                                                all_14_44_df = all_14_44, 
                                                columns_to_merge = ['regionName', 'ЦА'], 
                                    )
        # Новый датафрейм для аудитории "Все 18+". Перезаписываем его в словарь
        results['All 18+'] = all_18_new
        # Новый датафрейм для аудитории "Все 14-44". Перезаписываем его в словарь
        results['All 14-44'] = all_14_44_new

        # ШАГ. 3. Финальное преобразование таблиц
        for bca, df in results.items():
            df['Date'] = target_date_str
            df = df[['Date', 'tvCompanyName', 'Share']]
            results[bca] = df.pivot(index = 'Date', columns = 'tvCompanyName', values = 'Share').reset_index()
        
        # ШАГ. 4. Выстраивание нужного порядка колонок
        results = {
            bca: results[bca][cols]
            for bca, cols in columns_order.items()
            if bca in results
        }

        if save_to_output_file:
            print(Color.BOLD + Color.VIOLET + "=== СОХРАНЯЮ ВЫГРУЖЕННЫЕ ДАННЫЕ В ВЫХОДНЫЕ ФАЙЛЫ ===" + Color.END)

            # ШАГ. 5. Обновление файла с историческими данными
            share_old = pd.read_excel(historical_filepath, sheet_name = None, index_col = 0)

            data_old = share_old.copy()

            for bca, df in data_old.items():
                # Проверка на то, что последние две строчки в исходном DataFrame различны
                if len(data_old[bca]) >= 2 and data_old[bca].iloc[-1][0] == data_old[bca].iloc[-2][0]:
                    data_old[bca] = data_old[bca].iloc[:-1]  # Удаляем только последнюю дублирующуюся строку, а не две
                
                # Join выгрузки и исходного DataFrame
                if bca in results:  # Проверяем, что ключ существует в новых данных
                    if len(data_old[bca]) > 0 and len(results[bca]) > 0:
                        # Проверяем, не дублируется ли последняя дата
                        if data_old[bca].iloc[-1][0] == results[bca].iloc[0][0]:
                            # Если даты совпадают, объединяем без дубликата
                            res = pd.concat([data_old[bca].iloc[:-1], results[bca]], ignore_index = True)
                        else:
                            # Если даты разные, просто объединяем
                            res = pd.concat([data_old[bca], results[bca]], ignore_index = True)
                        
                        # Сохраняем результат обратно в словарь
                        data_old[bca] = res

            File(historical_filepath).to_file(data_old)
            
            try:
                writer = pd.ExcelWriter(historical_filepath, engine = 'xlsxwriter')
                for key, df in data_old.items():
                    Table(df = df).make_style_of_table(writer = writer, 
                                                    sheet_name = key, 
                                                    width_col_1 = 4.5, 
                                                    width_col_2 = 13.43, 
                                                    width_col_3 = 13.0)
                writer.close()
                print("✅ Файл успешно сохранен")

            except Exception as e:
                print(f"⚠️ Ошибка при сохранении: {e}")

        return data_old


    

    def make_calculation_several_periods(
            self,
            periods: dict,
            json_tasks_by_periods: dict
        ):
        """
            Метод для выгрузки данных по различным периодам.

            Параметры:
            ----------
                periods: dict
                    Словарь, где ключ - название периода, значение - период выгрузки в формате mediascope_api.
                json_tasks_by_periods: dict
                    Словарь из задач в формате JSON для отправки на сервер для каждого периода.  
            
            Returns:
            ----------
                self.results: dict
                    Словарь датафреймов, где ключ - название периода, значение - таблица DataFrame
        """
        self.results = {}
        for period, json_tasks in json_tasks_by_periods.items():

            import time
            start = time.perf_counter()

            self.date_filter = periods[period]

            period_results = {}
            export = partial(self.export_one, export_func = self.export_period, statistics = ['Share'])

            with ThreadPoolExecutor(max_workers = 8) as executor:
                futures = {
                    executor.submit(export, bca, task_json): bca
                    for bca, task_json in json_tasks.items()
                }
                for future in as_completed(futures):
                    bca, df = future.result()
                    period_results[bca] = df
            
            # Итоговый датафрейм с выгрузокй для одного периода
            res_df =  pd.concat(period_results.values())

            end = time.perf_counter()
            print(Color.BLUE + f'✅ 🏁 Время расчета задач по факту вышло: {((end - start) / 60.):0.2f} мин.' + Color.END)
            print( Color.center_text(Color.BOLD + Color.GREEN + '\n === ДАННЫЕ ВЫГРУЖЕНЫ. ДЕЛАЮ ПРЕОБРАЗОВАНИЯ. ===' + Color.END) )
            print('\n')

            mask_all_18 = res_df['ЦА'] == 'All 18+'
            all_18 = res_df[mask_all_18].reset_index(drop = True)


            mask_all_14_44 = res_df['ЦА'] == 'All 14-44'
            all_14_44 = res_df[mask_all_14_44].reset_index(drop = True)

            init_df = res_df[~(mask_all_18 |mask_all_14_44)].reset_index(drop = True)

            all_18_new, all_14_44_new = self.sum_2_channels(
                                                all_18, all_14_44, 
                                                ['regionName', 'ЦА']
                                    )
            res_df_new = pd.concat([init_df, all_18_new, all_14_44_new]).reset_index(drop = True)

            # Название новой колокни с периодом
            column_new = Dates_Operations(self.date_filter).generate_date_interval('Доля')

            # Переименование столбцов
            res_df_new.rename(
                columns = {
                    'Share': column_new,
                    'regionName': 'City',
                    'ЦА': 'BCA'
                }, 
                inplace = True
            )
            # Замена значений на русские в столбце ЦА
            res_df_new['BCA'].replace(BCA_NEED_REPLACE, inplace = True)

            # Переупорядочивание колонок. Колонку с периодом принудительно добавляем в конец
            new_columns_order = [c for c in res_df_new.columns if c != column_new] + [column_new]
            res_df_new = res_df_new[new_columns_order]

            values = ['ТЕЛЕКАНАЛ 78 (САНКТ-ПЕТЕРБУРГ)', 'САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)']

            mapping = {
                'ТЕЛЕКАНАЛ 78 (САНКТ-ПЕТЕРБУРГ)': 'ТЕЛЕКАНАЛ 78 САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)',
                'САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)': 'ТЕЛЕКАНАЛ САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)'
            }

            present = res_df_new.loc[
                res_df_new['tvCompanyName'].isin(values), 'tvCompanyName'
            ].unique()

            if len(present) == len(values):
                print(f"Делаю замену для обоих каналов: {list(present)}")
            elif len(present) > 0:
                print(f"Найден только: {list(present)}")
            else:
                print("Ни одного из значений нет — замена не нужна")

            # сама замена безопасна в любом случае:
            if len(present) > 0:
                res_df_new['tvCompanyName'] = res_df_new['tvCompanyName'].replace(mapping)

            result_df = res_df_new.sort_values(by = ['tvCompanyName', 'City'])

            self.results[period] = result_df.reset_index(drop = True)

        return self.results
    

    @staticmethod
    def append_monthly_to_file(current_year: int, df: pd.DataFrame, columns_order: str, filepath_by_months: str):
        """
            Новый метод для получения данных за фактическую часть месяца (за весь прошлый месяц).

            Параметры:
            ----------
                current_year: int
                    Номер текущего года для генерации месяца в формате "Январь 2021".
                df: pd.DataFrame
                    Датафрейм с выгрузкой фактической части месяца, которая была получена в выгрузке для руководителей групп.
                columns_order: str
                    Путь к файлу с порядком столбцов итоговых таблиц.
                filepath_by_months: str
                    Путь к файлу с фактическими данными по месяцам.
            
            Returns:
            ----------
                data_old: dict
                    Словарь датафреймов, где ключ - название ЦА, значение - DataFrame c данными.
        """
        # Загрузка порядка столбцов
        loaded_dict_columns = Dict_Operations.load_pkl_file(columns_order)

        data_anal = df.copy()

        # Получаем название столбца с периодом
        period_column = data_anal.columns[-1]

        # Разбиваем строку с периодом на подстроку с целью извлечения месяца
        text = period_column
        splitted = text.split(' ')
        month = splitted[-1]
        # Приводим название к первоначальному виду и добавляем текущий год
        morph = pmrph.MorphAnalyzer()
        month_normalized = morph.parse(month)[0].normal_form.capitalize()
        period_column_new = month_normalized + ' ' + str(current_year)

        # Переименовываем названия БЦА и оставляем только нужные колонки для дальнейшего анализа
        gender_map = {
            'ВСЕ 18+': 'All 18+', 
            'ВСЕ 14-59': 'All 14-59', 
            'Ж 25-59': 'W 25-59', 
            'ВСЕ 25-54': 'All 25-54', 
            'ВСЕ 6-54': 'All 6-54', 
            'ВСЕ 10-45': 'All 10-45', 
            'ВСЕ 14-54': 'All 14-54', 
            'ВСЕ 14-44': 'All 14-44', 
            'ВСЕ 4-45': 'All 4-45', 
            'Ж 14-44': 'W 14-44', 
            'ВСЕ 25-49': 'All 25-49', 
        }
        data_anal.rename(columns = {'BCA': 'БЦА'}, inplace = True)
        data_anal['БЦА'] = data_anal['БЦА'].map(gender_map)
        data_anal = data_anal[['tvCompanyName', 'БЦА', period_column]]

        # Переименовываем названия локальных каналов
        data_anal['tvCompanyName'].replace(
                        {
            'ТЕЛЕКАНАЛ 78 САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)': 'ТЕЛЕКАНАЛ 78 (САНКТ-ПЕТЕРБУРГ)', 
            'ТЕЛЕКАНАЛ САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)': 'САНКТ-ПЕТЕРБУРГ (САНКТ-ПЕТЕРБУРГ)'
            }, 
            inplace = True
            )

        # Создаем словарь, в котором в дальнейшем будем выстраивать нужный порядок столбцов
        bca_unique = data_anal['БЦА'].unique()
        results_data_anal = {}
        for bca in bca_unique:
            df = data_anal[data_anal['БЦА'] == bca].reset_index(drop = True)
            df.drop('БЦА', axis = 1, inplace = True)
            df = df.T
            
            headers = df.iloc[0].tolist()  # берем всю вторую строку
            # Третья строка (индекс 2) - это данные
            data = df.iloc[1].tolist()     # берем всю третью строку
            # Создаем DataFrame с одной строкой данных
            
            result = pd.DataFrame([data], columns = headers)
            result.insert(0, 'Date', period_column_new)
            
            results_data_anal[bca] = result


        dict_data_new = Dict_Operations(results_data_anal).rename_columns_in_dict_with_df(loaded_dict_columns)

        # Сохранение данных в файл
        data_old = pd.read_excel(filepath_by_months, sheet_name = None, index_col = 0)
        for bca, df in data_old.items():
            # Проверка на то, что последние две строчки в исходном DataFrame различны
            if len(data_old[bca]) >= 2 and data_old[bca].iloc[-1][0] == data_old[bca].iloc[-2][0]:
                data_old[bca] = data_old[bca].iloc[:-1]  # Удаляем только последнюю дублирующуюся строку, а не две
            
            # Join выгрузки и исходного DataFrame
            if bca in dict_data_new:  # Проверяем, что ключ существует в новых данных
                if len(data_old[bca]) > 0 and len(dict_data_new[bca]) > 0:
                    # Проверяем, не дублируется ли последняя дата
                    if data_old[bca].iloc[-1][0] == dict_data_new[bca].iloc[0][0]:
                        # Если даты совпадают, объединяем без дубликата
                        res = pd.concat([data_old[bca].iloc[:-1], dict_data_new[bca]], ignore_index=True)
                    else:
                        # Если даты разные, просто объединяем
                        res = pd.concat([data_old[bca], dict_data_new[bca]], ignore_index=True)
                    
                    # Сохраняем результат обратно в словарь
                    data_old[bca] = res
                    
        #Сохранение в файл
        File(filepath_by_months).to_file(data_old)

        #Придание внешнего вида итоговой таблице
        try:
            writer = pd.ExcelWriter(filepath_by_months, engine = 'xlsxwriter')
            for key, df in data_old.items():
                Table(df = df).make_style_of_table(writer = writer, 
                                                sheet_name = key, 
                                                width_col_1 = 4.5, 
                                                width_col_2 = 13.43, 
                                                width_col_3 = 13.0)
            writer.close()
            print("✅ Файл успешно сохранен")

        except Exception as e:
            print(f"⚠️ Ошибка при сохранении: {e}")
        
        return data_old
    

    def updated_monthly_data_by_condition(
            self, 
            today_month: int, 
            today_day: int, 
            current_year: int,
            periods: dict,
            results_from_teamlead: dict,
            json_tasks: dict,
            columns_order_filepath: str,
            monthly_data_filepath: str
        ):
        """
            Метод для обновления файла с фактическими месячными данными по по определенным условиям.

            Параметры:
            ----------
                today_month: int
                    Номер текущего месяца.
                today_day: int
                    Номер сегодняшнего дня в текущем месяце.
                current_year: int
                    Номер текущего года.
                periods: dict
                    Словарь с периодами для выгрузки, где ключ - название периода, значение - период для выгрузки в формате mediascope_api.
                results_from_teamlead: dict
                    Словарь с выгрузкой для руководителей групп.
                columns_order_filepath: str
                    Путь к файлу с порядком колонок.
                monthly_data_filepath: str
                    Путь к файлу с фактическими данными по месяцам.
        """
        from datetime import date
        today = date.today()

        data_new = pd.DataFrame()

        # Если текущий месяц январь
        if today_month == 1:
            print(Color.BOLD + Color.SEA_GREEN + f"Текущий месяц ЯНВАРЬ {current_year} года." + Color.END)

            # Если сегодня последнее обновление в текущем месяце
            if today_day > 20 and today_day <= 31:
                print(Color.BOLD + f"Сегодня последнее обновление в месяце ЯНВАРЬ." + Color.END)

                # Если раннее выгрузили данные по этому периоду
                if len(results_from_teamlead['fact_month']) != 0:

                    print(
                        "Раннее были выгружены данные за фактическую часть месяца в ЯНВАРЕ. " + \
                        "Использую их для обновления файла с фактом. Пожалуйста, подождите..."
                    )

                    # Формирование выходной таблицы
                    data_new = ExportService.append_monthly_to_file(
                                    current_year,
                                    results_from_teamlead['fact_month'],
                                    columns_order_filepath,
                                    monthly_data_filepath
                                )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
                
                else:
                    print(
                        Color.BOLD + Color.DARK_RED + \
                        "❌ Не нашлось раннее выгруженных данных за этот период! Делаю выгрузку данных. Пожалуйста, подождите ... " + \
                        Color.END
                    )

                    self.date_filter = periods['fact_month']
                    data_new = self.make_api_calculation_period(
                        json_tasks, 
                        columns_order_filepath,
                        monthly_data_filepath
                    )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
            
            elif today_day > 1 and today_day <= 15:
                print(Color.BOLD + f"Сегодня предыдущий месяц закрываем! Пожалуйста, подождите ..." + Color.END)

                # Если раннее выгрузили данные по этому периоду
                if len(results_from_teamlead['full_month']) != 0:

                    print(
                        f"Раннее были выгружены данные за прошлый месяц ДЕКАБРЬ {current_year - 1} года. " + \
                        "Использую их для обновления файла с фактом. Пожалуйста, подождите..."
                    )

                    # Формирование выходной таблицы
                    data_new = ExportService.append_monthly_to_file(
                                    current_year - 1,
                                    results_from_teamlead['full_month'],
                                    columns_order_filepath,
                                    monthly_data_filepath
                                )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
                
                else:
                    print(
                        Color.BOLD + Color.DARK_RED + \
                        "❌ Не нашлось раннее выгруженных данных за этот период! Делаю выгрузку данных. Пожалуйста, подождите ... " + \
                        Color.END
                    )

                    self.date_filter = periods['full_month']
                    data_new = self.make_api_calculation_period(
                        json_tasks, 
                        columns_order_filepath,
                        monthly_data_filepath
                    )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
            
            else:
                print('Пропускаю этот блок ....')
        
        # Если текущий месяц НЕ январь
        else:
            #Если сегодня последнее обновление в текущем месяце
            if today_day > 20 and today_day <= 31:

                today_month = f"{MONTHS_RU[today.month]}"
                #morph = pmrph.MorphAnalyzer()
                #month_normalized = morph.parse(today_month)[0].inflect({'loct'}).word.capitalize()

                print(Color.BOLD + Color.ROYAL_BLUE + f"Сегодня последнее обновление в месяце {today_month} {current_year} года." + Color.END)

                # Если раннее выгрузили данные по этому периоду
                if len(results_from_teamlead['fact_month']) != 0:

                    print(
                        f"Раннее были выгружены данные за фактическую часть месяца {today_month} {current_year} года. " + \
                        "Использую их для обновления файла с фактом. Пожалуйста, подождите..."
                    )

                    # Формирование выходной таблицы
                    data_new = ExportService.append_monthly_to_file(
                                    current_year,
                                    results_from_teamlead['fact_month'],
                                    columns_order_filepath,
                                    monthly_data_filepath
                                )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
                
                else:
                    print(
                        Color.BOLD + Color.DARK_RED + \
                        "❌ Не нашлось раннее выгруженных данных за этот период! Делаю выгрузку данных. Пожалуйста, подождите ... " + \
                        Color.END
                    )

                    self.date_filter = periods['fact_month']
                    data_new = self.make_api_calculation_period(
                        json_tasks, 
                        columns_order_filepath,
                        monthly_data_filepath
                    )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
            
            #Если сегодня месяц закрываем
            elif today_day > 1 and today_day <= 10:

                last_month = f"{MONTHS_RU[today.month - 1]}"

                print(Color.BOLD + f"Сегодня предыдущий месяц {last_month} закрываем! Пожалуйста, подождите ..." + Color.END)

                # Если раннее выгрузили данные по этому периоду
                if len(results_from_teamlead['full_month']) != 0:

                    print(
                        f"Раннее были выгружены данные за прошлый месяц {last_month} {current_year} года. " + \
                        "Использую их для обновления файла с фактом. Пожалуйста, подождите..."
                    )

                    # Формирование выходной таблицы
                    data_new = ExportService.append_monthly_to_file(
                                    current_year,
                                    results_from_teamlead['full_month'],
                                    columns_order_filepath,
                                    monthly_data_filepath
                                )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
                
                else:
                    print(
                        Color.BOLD + Color.DARK_RED + \
                        f"❌ Не нашлось раннее выгруженных данных за {last_month} {current_year} года! Делаю выгрузку данных. Пожалуйста, подождите ... " + \
                        Color.END
                    )

                    self.date_filter = periods['full_month']
                    data_new = self.make_api_calculation_period(
                        json_tasks, 
                        columns_order_filepath,
                        monthly_data_filepath
                    )
                    print('Данные обновлены. Спасибо за ваше ожидание! 😊')
            
            else:
                print('Пропускаю этот блок ....')

        return data_new  



    def render_output_tables(
            self, 
            target_share_table: pd.DataFrame, 
            target_team_lead_table: pd.DataFrame, 
            output_file_1: str, 
            output_file_2: str):
        """
            Метод для записи результатов в файлы для генерации комментариев, а также для руководителей групп.

            Параметры:
            ----------
                target_share_table: pd.DataFrame
                    Таблица шаблон заготовка для генерации комментариев
                target_team_lead_table: pd.DataFrame
                    Таблица шаблон заготовка для руководителей групп
                output_file_1: str
                    Путь к файлу для генерации комментариев
                output_file_2: str
                    Путь к файлу для руководителей групп

            Returns:
            ----------
                res_cleaned: pd.DataFrame
                    Сформированная таблица для генерации комментариев
                team_lead_result_cleaned: pd.DataFrame
                    Сформированная таблица для руководителей групп
        """
        # Ключи, по которым будем делать merge
        keys = ['tvCompanyName', 'City', 'BCA']  # Ключевые столбцы, по которым делаем merge

        print(Color.VIOLET + 'Делаю таблицу для генерации комментариев.' + Color.END)
        
        # ШАГ 1. Создание таблицы путём join 4х выгруженных таблиц
        res = (
            self.results['fact_month'].merge(self.results['last_14_days'], on = keys, how = 'inner')
                                .merge(self.results['prev_14_before_last_14_days'], on = keys, how = 'inner')
                                .merge(self.results['prev_to_fact_month'], on = keys, how = 'inner')
        )

        # ШАГ 2. Merge с таблицей-шаблоном заготовки генерации комментариев
        comments_table = pd.merge(target_share_table, res, on = keys, how = 'inner')
        comments_table_sorted = comments_table.sort_values(by = ['Channel', 'City']).reset_index(drop = True)
        res_cleaned = comments_table_sorted.drop('tvCompanyName', axis = 1)

        only_target = set(target_share_table['Channel'].dropna()) - set(res_cleaned['Channel'].dropna())
        if only_target != set():
            print("Есть в target, нет в merged:", only_target)

        # ШАГ 3. Сохранение в файл выходной таблицы
        res_cleaned.to_excel(output_file_1)

        #Уставнока внешнего вида итоговой таблицы
        writer = pd.ExcelWriter(output_file_1, engine = 'xlsxwriter')
        Table(df = res_cleaned).make_style_of_table(writer = writer, 
                                            sheet_name = 'Sheet1', 
                                            width_col_1 = 4.5, 
                                            width_col_2 = 17.6, 
                                            width_col_3 = 20.0)
        writer.close()



        print(Color.VIOLET + 'Делаю таблицу для руководителей групп.' + Color.END)

        column_fact = self.results['fact_month'].columns[3]
        fact = self.results['fact_month'].rename(columns = {f'{column_fact}': 'Факт'})

        column_last_21_day = self.results['last_21_days'].columns[3]
        last_21_days = self.results['last_21_days'].rename(columns = {f'{column_last_21_day}': 'Последние 3 недели'})

        column_last_14_day = self.results['last_14_days'].columns[3]
        last_14_days = self.results['last_14_days'].rename(columns = {f'{column_last_14_day}': 'Последние 2 недели'})

        team_lead_res = Table.make_left_join(
            fact, last_21_days, last_14_days, 
            keys)
        team_lead_res.rename(columns = {'BCA': 'БЦА'}, inplace = True)


        team_lead_res['Месяц'] = datetime.now().replace(day = 1).strftime('%d %b %y')
        team_lead_result = pd.merge(target_team_lead_table, team_lead_res, on = ['tvCompanyName', 'БЦА'], how = 'left')
        team_lead_result.rename(columns = {'Телеканал': 'Сцепка 1'}, inplace = True)
        team_lead_result_cleaned = team_lead_result.drop('tvCompanyName', axis = 1)

        team_lead_result_cleaned.to_excel(
                        output_file_2,
                        sheet_name = 'Сводный', 
                        index = False)

        #Уставнока внешнего вида итоговой таблицы
        writer = pd.ExcelWriter(
            output_file_2,
            engine = 'xlsxwriter'
        )
        Table(df = team_lead_result_cleaned).make_style_of_table(
                                        writer = writer, sheet_name = 'Сводный', 
                                        width_col_1 = 4.5, width_col_2 = 17.6, width_col_3 = 20.0)
        writer.close()

        return res_cleaned, team_lead_result_cleaned