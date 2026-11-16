import pandas as pd
import multiprocessing as mp
from functools import reduce

from OMA_tools.io_data.operations import File, Table, Dict_Operations
from OMA_tools.regions.data_extraction.export_data import *
from OMA_tools.regions.ttv_forecast.ttv_constants import Holidays, Prophet_Constants
from OMA_tools.io_data.colors import *

from prophet import Prophet
import logging
logging.getLogger("prophet").setLevel(logging.ERROR)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger('log').setLevel(logging.INFO)
logging.getLogger("cmdstanpy").propagate = False
logging.getLogger("log").propagate = False
logging.getLogger("cmdstanpy").disabled = True


import warnings
warnings.filterwarnings('ignore')

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


class TTVEngine:
    """
        Класс для выгрузки и прогнозирования статистики TTV 
    """
    def __init__(self, date_filter: list):
        """
            Конструктор класса.

            Параметры:
            ----------
                date_filter: list
                    Временной период для выгрузки данных
        """
        self.date_filter = date_filter

        self.statistics = ['TTVRtgPer']
    

    def data_file_updation_by_days(
            self, 
            exported_ttv: dict, 
            columns_order_dict: dict, 
            file_path: str, 
            save_to_output_file = True
        ):
        """
            Метод для обновления файла с фактическими данными в разбивке по дням.

            Параметры:
            ----------
                exported_ttv: dict
                    Свежевыгруженные данные по TTV в разбивке по дням.
                columns_order_dict: dict
                    Словарь с порядком столбцов для таблиц
                file_path: str
                    Путь к файлу с фактическими данными.
                save_to_output_file: bool
                    Булева переменная. Если True, то производится сохранение в выходной файл. В противном случае нет. По умолчанию "True".
        """
        # ШАГ 1. Преобразования с данными. Приведение к виду, который указан в таблицу с историческими данными
        ttv_results = []
        for bca, df in exported_ttv.items():
            df['ЦА'] = df['ЦА'].replace(BCA_NEED_REPLACE)
            df['Канал'] = (df['regionName'] + ' ' + df['ЦА']).str.replace(' ВСЕ ', ' ', regex = False)

            df_pivoted = (
                df.drop_duplicates(subset=['Date', 'regionName'], keep = 'first')
                [['Date', 'Канал', 'TTVRtgPer']]
                .pivot(index = 'Date', columns = 'Канал', values = 'TTVRtgPer')
                .reset_index()
                .rename(columns = {'Date': 'Дата'})
            )
            ttv_results.append(df_pivoted)

        # ШАГ 2. JOIN всех таблиц по всем аудиториям в одну большую таблицу для удобства
        merged = reduce(
            lambda l, r: pd.merge(l, r, on = 'Дата', how = 'outer'),
            ttv_results,
        )

        # ШАГ 3. Выстраивание нужного порядка столбцов для каждой группы
        result = {
            bca: merged[['Дата'] + cols]
            for bca, cols in columns_order_dict.items()
        }

        # ШАГ 4. Формирование выходного словаря для дальнейшей работы
        total_new = {
            'All 4-45': result['All 4-45'], 
            'All 6-54': result['All 6-54'], 
            'All 14-54': result['All 14-54'], 
            'All 18+': result['All 18+'], 
            'EKB_NN_KZN': Table.make_left_join(
                                    result['Ekaterinburg'], 
                                    result['Kazan'], 
                                    result['Nizniy_Novgorod'], 
                                    key = 'Дата'
                                    ), 
            'Novosibirsk': result['Novosibirsk'], 
            'SaintPetersburg': result['SaintPetersburg']}
        

        # ШАГ 5. Чтение файла с историческими данными и его обновление
        if save_to_output_file:
            # Чтение старых данных из исторического файла
            total_old = pd.read_excel(file_path, sheet_name = None)

            # Обновление таблиц
            updated_dict = {}
            for bca, old_df in total_old.items():
                new_data = total_new[bca]
                
                if len(new_data) != 0:
                    old_df['Дата'] = pd.to_datetime(old_df['Дата'])
                    new_data['Дата'] = pd.to_datetime(new_data['Дата'])
                    updated_dict[bca] = Builder().update_table(old_df, new_data, 'Дата')
            
            # Сохранение в файл обновленных данных
            Builder().save_tables_to_excel(updated_dict, file_path, 'Дата')

        return updated_dict


    def calculate_fact_month(
            self, 
            tasks_json_full: dict, 
            girls_cities_file_path: str,
            filepath_output: str,
            statistics = ['TTVRtgPer']
        ):
        """
            Метод для выгрузки фактической части месяца по TTV.

            Параметры:
            ----------
        """
        import time
        start = time.perf_counter()

        exp_serv = ExportService(self.date_filter)
        export = partial(
            exp_serv.export_one,
            export_func = exp_serv.export_period,
            statistics = statistics
        )

        fact_month_results = {}
        with ThreadPoolExecutor(max_workers = 8) as executor:
            futures = {
                executor.submit(export, bca, task_json): bca
                for bca, task_json in tasks_json_full.items()
            }
            for future in as_completed(futures):
                bca, df = future.result()
                fact_month_results[bca] = df
        
        end = time.perf_counter()
        print('\n')
        print(Color.BOLD + Color.GREEN + '=== ДАННЫЕ ВЫГРУЖЕНЫ. ===' + Color.END)
        print(f'Время расчета задач по факту вышло: {((end - start) / 60.):0.2f} мин.')

        ttv_results = []
        for bca, df in fact_month_results.items():
            df['ЦА'] = df['ЦА'].replace(BCA_NEED_REPLACE)
            df['Регион'] = (df['regionName'] + ' ' + df['ЦА']).str.replace(' ВСЕ ', ' ', regex = False)
            df = df[['Регион', 'TTVRtgPer']]
            ttv_results.append(df)
        full_table = pd.concat(ttv_results).reset_index(drop = True)
        full_table.rename(columns = {'TTVRtgPer': 'TTV'}, inplace = True)
        
        reference_table = pd.read_excel(girls_cities_file_path, sheet_name = None)

        results_by_girls = {}
        for girl, df in reference_table.items():
            df['Регион'] = df['Регион'].str.upper()
            result = pd.merge(df, full_table, on = ['Регион'], how = 'inner')
            results_by_girls[girl] = result.set_index('Регион').T
        
        try:
            File(filepath_output).to_file(results_by_girls)
            print('✅ Файл успешно сохранен')
            
        except Exception as e:
            print(f"⚠️ Ошибка при сохранении: {e}")
        
        return results_by_girls



class TTVForecast:
    """
        Класс для прогнозирования Регионального TTV с использованием модели Prophet.
    """
    def __init__(self, last_predict_date: str, total_dict: dict):
        """
            Атрибут класса
            Args:
                last_predict_date: str: последняя дата предсказания.
        """
        self.last_predict_date = last_predict_date
        self.total_dict = total_dict
        self.proph_consts = Prophet_Constants()
    

    def get_predictions(self, key: str):
        """
            Генерация количества предсказываемых дней.
            
            Args:
                df: словарь из датафрейм с TTV по дням.
            Returns:
                predictions: int: Количество предсказываемых дней 
                last fact date (last_fact_date): последняя фактическая дата.
        """
        last_fact_date = self.total_dict[key].date.max()
        #последняя дата предсказываемого периода
        self.last_predict_date = pd.to_datetime(self.last_predict_date, format = '%d.%m.%Y')
        last_fact_date = pd.to_datetime(last_fact_date, format = '%Y-%m-%d')
        predictions = self.last_predict_date - last_fact_date
        predictions = predictions.days
        return predictions, last_fact_date
    

    def convert_columns_to_prophet_format(self):
        """
            Метод для конвертации столбцов в датафреймах в формат Prophet ('ds' и 'y').
            
            Args:
                dict_of_total: словарь из датафреймов с TTV по дням.
            Returns:
                dict_of_total: словарь из датафреймов с TTV по дням с переименованными колонками 'ds' and 'y'.
                ds_dict: словарь из дат
        """
        dict_of_total = self.total_dict
        ds_dict = {}
    
        for key, df in dict_of_total.items():
            # Создаем новый DataFrame с переименованными колонками
            new_df = df.rename(columns = {'date': 'ds'})
            
            # Сохраняем даты отдельно
            ds_dict[key] = pd.to_datetime(new_df['ds'], format = '%Y-%m-%d')
            
            # Обновляем словарь
            dict_of_total[key] = new_df
    
        return dict_of_total, ds_dict
    

    def get_cond__and__train_df(self, ds_dict, dict_of_total, last_fact_date):
        """
        This function returns condition and datasets of training data which were selected by condition.
        
        Args:
            ds_dict: DataFrame with dates only
            dict_of_total: dict of total DataFrames, where column 'date' was renamed to 'ds' and column 'ГОРОД БЦА' was renamed to 'y'.
            last_fact_date: the last fact date in the datasets.
            
        Returns:
            cond_df: dict of condition on choosing the right history data
            train_df: dict of train_df DataFrames depending on condition (cond_df).
        """
        # Предварительная подготовка ключей и дат
        cond_keys = ['All 4-45', 'All 6-54', 'All 14-54', 'All 18+', 
                    'EKB_NN_KZN', 'Novosibirsk', 'SaintPetersburg']
        
        cond_df = {key: [] for key in cond_keys}
        train_df = {key: [] for key in cond_keys}
        
        # Конвертируем один раз
        ds_dict_converted = Dict_Operations(ds_dict).replace_keys_in_dict(cond_keys)
        dict_of_total_converted = Dict_Operations(dict_of_total).replace_keys_in_dict(cond_keys)
        
        # Предварительно конвертируем last_fact_date
        last_fact_date_dt = pd.to_datetime(last_fact_date)
        
        # Один проход по данным
        for bca_num, (idx, dates) in enumerate(self.proph_consts.cond_date_count.items()):
            for date_num, date in enumerate(dates):
                date_dt = pd.to_datetime(date)  # Конвертируем дату один раз
                
                # Создаем условие
                condition = (ds_dict_converted[idx] <= last_fact_date_dt) & (ds_dict_converted[idx] >= date_dt)
                
                # Применяем условие
                cond_df[idx] = condition
                train_df[idx] = dict_of_total_converted[idx][condition]
        
        return train_df
    

    def get_forecast(self, train_df, predictions, last_fact_date):
        """
            Осуществление процесса прогнозирвоания для каждой БЦА.
        """
        def process_bca(bca_data):
            bca, df = bca_data
            results = pd.DataFrame()
            for icol, col in enumerate(df.columns[1:]):
                tmp_df = pd.concat([df['ds'], df.iloc[:, icol + 1]], axis = 1, keys = ['ds', 'y'])
                
                m = Prophet()
                if self.proph_consts.cond_holidays[bca]:
                    m = Prophet(holidays = Holidays().holidays)
                m.fit(tmp_df)
                
                future = m.make_future_dataframe(periods = predictions)
                forecast = m.predict(future)
                
                tmp_df.columns = ['ds', 'yhat']
                tmp_df['yhat_lower'] = tmp_df['yhat']
                tmp_df['yhat_upper'] = tmp_df['yhat']
                
                tmp = forecast.loc[:, ['ds', 'yhat', 'yhat_lower', 'yhat_upper']]
                forecast_cut = tmp[tmp.ds > last_fact_date]
                tmp_df = tmp_df[tmp_df.ds >= dt.datetime(last_fact_date.year - 4, 1, 1)]
                result = pd.concat([tmp_df, forecast_cut], axis=0)
                result['bca'] = df.columns[icol + 1]
                
                results = pd.concat([result, results]).reset_index(drop = True)
            return bca, results
        
        # Используем многопоточность для параллельной обработки BCA
        with ThreadPoolExecutor(max_workers = mp.cpu_count()) as executor:
            results = list(executor.map(process_bca, train_df.items()))
        
        results_df = {bca: result for bca, result in results}
        return results_df
    

    def make_forecast(self, path: str, file_names: list):
        """
            Пайплайн для прогнозирования регионального TTV
        """
        print(Color.BOLD + Color.VIOLET + '=== 🚀 НАЧИНАЮ МАШИННЫЙ ПРОГНОЗ TTV ===' + Color.END)
        print('\n')

        predictions, last_fact_date = self.get_predictions('All 4-45')
        df, ds_dict = self.convert_columns_to_prophet_format()
        train_df = self.get_cond__and__train_df(
                                                ds_dict = ds_dict,
                                                dict_of_total = df,
                                                last_fact_date = last_fact_date)
        results_df = self.get_forecast(
                                train_df = train_df,
                                predictions = predictions,
                                last_fact_date = last_fact_date)  
        
        print('Прогноз завершён! Сохраняю результаты в выходные файлы. Проверьте соответствующую папку. Файлы должны обновиться.')
        File.to_file_from_dict(
                        path = path,
                        dict_data = results_df,
                        file_names = file_names)
        
        print(Color.BOLD + Color.VIOLET + '=== Данные выгружены и сохранены. Спасибо за ваше ожидание! 😊 ===' + Color.END)