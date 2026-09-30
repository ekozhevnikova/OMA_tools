import pandas as pd
import numpy as np
from datetime import timedelta
from statsmodels.tsa.stattools import adfuller, kpss
from scipy import stats
from scipy.stats import kendalltau
from sklearn.preprocessing import MinMaxScaler




class TimeSeriesTransformer:
    """
        Класс для предобработки Временных Рядов.
    """
    def __init__(self, series):
        self.series = series

    
    @staticmethod
    def is_stationary(series, alpha = 0.05):
        """
            Проверка стационарности ряда.
            Args:
                data: 
            Return:
                stationary: bool: True если ВР стационарен и False в противном случае
                p_value: p-value
        """
        result = adfuller(series)
        stationary = result[1] < alpha
        p_value = result[1]
        return stationary, p_value

    
    def check_variance_stability_and_log_transform(self,
                                                   n_months: int = 6, 
                                                   threshold: float = 1.5, 
                                                   alpha: float = 0.05):
        """
            Проверяет, сильно ли менялась дисперсия в последние n месяцев.
            Если дисперсия нестабильна, проводится логарифмирование ряда.
            
            Args:
                data : исходный DataFrame
                target: str: столбец с целевой переменной
                date_column: str: столбец с названием Даты
                n_months : int: Количество последних месяцев для анализа
                threshold : float: Пороговое значение для отношения дисперсий
                alpha : float: Уровень значимости для статистических тестов
            Returns:
                transformed_df: прологарифмированный временной ряд
                   или
                data: исходный временной ряд
        """
        #Проверка входных данных
        if not isinstance(self.series, pd.Series):
            raise ValueError("Входные данные должны быть pandas.Series")
        
        if len(self.series) < n_months * 2:
            n_months = n_months - 1
            #raise ValueError("Ряд слишком короткий для анализа")
        
        #Убедимся, что индекс - datetime
        if not isinstance(self.series.index, pd.DatetimeIndex):
            raise ValueError("Индекс должен быть DatetimeIndex")
        
        #Разделяем ряд на две части: последние n месяцев и все предыдущие
        latest_date = self.series.index.max()
        cutoff_date = latest_date - pd.DateOffset(months = n_months)
        
        recent_data = self.series[self.series.index > cutoff_date]
        older_data = self.series[self.series.index <= cutoff_date]
        
        #Проверяем, что в обеих частях достаточно данных
        if len(recent_data) < 2 or len(older_data) < 2:
            return False, {"error": "Недостаточно данных для анализа"}
        
        #Вычисляем дисперсии
        var_recent = np.var(recent_data)
        var_older = np.var(older_data)
        
        #1.Проверка отношения дисперсий (F-тест)
        variance_ratio = max(var_recent, var_older) / min(var_recent, var_older)
        
        #2.Тест Левене на равенство дисперсий
        try:
            levene_stat, levene_p = stats.levene(recent_data, older_data)
        except:
            levene_stat, levene_p = np.nan, np.nan
        
        #3.Тест Бартлетта (более чувствителен к нормальности)
        try:
            bartlett_stat, bartlett_p = stats.bartlett(recent_data, older_data)
        except:
            bartlett_stat, bartlett_p = np.nan, np.nan
        
        #Принимаем решение о необходимости логарифмирования
        log_transform_recommended = (
            (variance_ratio > threshold) or
            (not np.isnan(levene_p) and levene_p < alpha) or
            (not np.isnan(bartlett_p) and bartlett_p < alpha)
        )
        
        #Детальная информация для отладки
        details = {
            'variance_ratio': variance_ratio,
            'levene_p_value': levene_p,
            'bartlett_p_value': bartlett_p,
            'recent_variance': var_recent,
            'older_variance': var_older,
            'recent_mean': np.mean(recent_data),
            'older_mean': np.mean(older_data),
            'n_recent': len(recent_data),
            'n_older': len(older_data),
            'threshold': threshold,
            'alpha': alpha
        }
        if log_transform_recommended:
            # Добавляем небольшую константу, чтобы избежать log(0)
            constant = 1e-10 if self.series.min() <= 0 else 0
            transformed_series = np.log(self.series + constant)
            
            return transformed_series, log_transform_recommended
        else:
            return self.series, log_transform_recommended

    
    def find_optimal_differentiation(self, max_diff: int = 3):
        """
            Поиск оптимального порядка дифференцирования.
            Args:
            Return:
            
        """
        best_diff = 0
        best_p_value = 1.0
        
        for diff_order in range(0, max_diff + 1):
            if diff_order == 0:
                diff_series = self.series.copy()
            else:
                diff_series = self.series.diff(diff_order).dropna()
            
            if len(diff_series) < 10:
                continue
            
            stationary, p_value = TimeSeriesTransformer.is_stationary(diff_series)
            
            if stationary and p_value < best_p_value:
                best_diff = diff_order
                best_p_value = p_value
        return best_diff, best_p_value


    def make_stationary(self, max_diff: int = 3):
        """
            Приведение ряда к стационарному видую
        """
        stationary, p_value = TimeSeriesTransformer.is_stationary(self.series)

        if not stationary:
            print('Временной ряд не стационарен. Начинаю предобработку ...')

            series_modified, log_transform = self.check_variance_stability_and_log_transform()

            self.series = series_modified
            # Поиск оптимального порядка дифференцирования
            optimal_diff, p_value = self.find_optimal_differentiation(max_diff)
            diff_order = optimal_diff
            
            # Сохраняем первые значения для восстановления
            first_values = []
            if optimal_diff > 0:
                for i in range(optimal_diff):
                    first_values.append(self.series.iloc[i])
            
            # Применяем дифференцирование
            if optimal_diff == 0:
                stationary_series = self.series
            else:
                stationary_series = self.series.diff(optimal_diff).dropna()
            print('Ряд приведен к стационарному виду.')
            return stationary_series, optimal_diff, p_value, log_transform
        else:
            optimal_diff = 0
            p_value = 0
            log_transform = False
            return self.series, optimal_diff, p_value, log_transform
    

    @staticmethod
    def find_missing_dates(dates: list) -> int:
        """
            Функция для поиска пропущенных дат в хронологии дат.
            Args:
                dates: список дат в DataFrame
            Returns:
                len(gaps): длину списка из пропусков.
        """
        gaps = []  # Здесь будем хранить найденные пропуски

        # Проверяем промежутки между последовательными датами
        for i in range(1, len(dates)):
            prev_date = dates[i - 1]
            curr_date = dates[i]
            
            # Вычисляем ожидаемую следующую дату (предыдущая дата + 1 день)
            expected_date = prev_date + timedelta(days = 1)
            
            # Если текущая дата не совпадает с ожидаемой, значит есть пропуск
            if curr_date != expected_date:
                # Определяем все пропущенные даты в промежутке
                gap_duration = (curr_date - prev_date).days - 1
                current_gap_date = expected_date
                
                for _ in range(gap_duration):
                    gaps.append(current_gap_date.strftime('%Y-%m-%d'))
                    current_gap_date += timedelta(days = 1)
        return len(gaps)


    @staticmethod
    def fix_dates_in_dataframe(data, date_column: str = 'Date'):
        """
            Используется, если есть пропуски в датах. Формирует заново хронологическую последовательность из дат.
            Args:
                data: Исходный сломанный датафрейм, в котором два столбца: столбец с датой + столбец с таргетом.
                date_column: str: Название столбца с датой.
            Return:
                fixed_data: исправленный DataFrame.
        """
        data_ = data.copy()
        data_.set_index(date_column, inplace = True)

        # Создаем полный ежедневный индекс за нужный период
        start_date = data_.index.min()
        end_date = data_.index.max()
        full_index = pd.date_range(start = start_date, end = end_date, freq = 'D')
        #Создаем новый DataFrame
        full_df = pd.DataFrame(index = full_index)
        
        # Объединяем с исходными данными
        result_df = full_df.merge(data_, left_index = True, 
                                right_index = True, how = 'left').reset_index().rename(columns = {'index': date_column})
        
        #Отбираем только нужные столбцы
        fixed_data = result_df[[date_column, 'Share']]
        return fixed_data
    

    @staticmethod
    def check_and_fix_dates(df, date_column: str = 'Date', target_column: str = 'Share'):
        """
            Проверяет хронологический порядок дат и заменяет пропуски на медианное значение.
            Args:
                df (DataFrame): Исходный сломанный датафрейм, в котором два столбца: столбец с датой + столбец с таргетом.
                date_column: str: название столбца с датами
                target_column: str: название столбца с таргетом
            Returns:
                DataFrame: обработанный датафрейм
        """
        # Преобразуем колонку в datetime формат
        df[date_column] = pd.to_datetime(df[date_column])
        
        num_of_gaps = TimeSeriesTransformer.find_missing_dates(list(df[date_column]))
        if num_of_gaps != 0:
            print('Есть пропуски в датах. Начинаю заполнение...')
            #Создается полный фулл дат в хронологическом порядке
            fixed_df = TimeSeriesTransformer.fix_dates_in_dataframe(df, date_column)
            #Заполняем пропуски следующим значением
            fixed_df = fixed_df.assign(FFill = fixed_df[target_column].ffill())
            df = fixed_df[[date_column, 'FFill']]
            df.rename(columns = {'FFill': target_column}, inplace = True)
            df = df.sort_values(by = date_column).reset_index(drop = True)
            if df[target_column].isnull().sum() == 0:
                print('Все пропуски заполнены.')
            else:
                print('Не удалось заполнить все пропуски. Попробуйте еще раз! Или измените подход.')
        df = df.sort_values(by = date_column).reset_index(drop = True)
        return df
    

    def check_scale_and_modify_scale_if_need(data, target_column: str = 'Share', date_column: str = 'Date'):
        """
            Проверяет на одинаковость масштаба данных.
            Args:
                data: Исходный сломанный датафрейм, в котором два столбца: столбец с датой + столбец с таргетом.
                target_column: str: название столбца с таргетом
                date_column: str: название столбца с датами
            Return:
                data: если масштаб одинаковый
                df: отмасштабированный data
        """

        target_values = list(data[target_column])

        #Разброс значений
        range_ratio = max(target_values) / min(target_values)
        #Стандартное отклонение
        std = np.std(target_values)
        #Среднее значение
        mean = np.mean(target_values)
        #коэффициент ковариации в %
        covariation = (std / mean) * 100

        if range_ratio > 10 and (covariation > 15 or std > 10):
            if covariation > 15 or std > 10:
                print('⚠️  Высокая волатильность! Признаки имеют РАЗНЫЙ масштаб! Требуется нормализация.')
                print('-' * 20)
                print('Нормализую ...')
                scaler = MinMaxScaler()
                X = data[target_column].values.reshape(-1, 1)
                scaler.fit(X)
                X_scaled = scaler.transform(X)
                scaled_values_list = [i[0] for i in X_scaled]
                data['target'] = scaled_values_list
                df = data[[date_column, 'target']]
                df.rename(columns = {'target': target_column}, inplace = True)
                return df, scaler
        else:
            print("Признаки имеют ОДИНАКОВЫЙ масштаб.")
            scaler = None
            return data, scaler
    

    def detect_outliers(data, target: str = 'Share'):
        """
            Функция для замены выбросов на значения медианы.
            Args:
                data: Исходный сломанный датафрейм, в котором два столбца: столбец с датой + столбец с таргетом.
                target: str: название столбца с таргетом
            Retuns:
                data: измененный/не измененный data
        """
        #Замена выбросов на значения медианы
        med = np.quantile(data[target], 0.5)
        values_init = list(data[target])
        
        Q1, Q3 = data[target].quantile([0.25, 0.75])
        IQR = Q3 - Q1
        lower_limit = Q1 - 1.5 * IQR
        upper_limit = Q3 + 1.5 * IQR  
        
        num_of_outlier_lower = sum(i > lower_limit for i in values_init)
        num_of_outlier_upper = sum(i > upper_limit for i in values_init)
        number = num_of_outlier_lower + num_of_outlier_upper
        if number > 0:
            print('В выборке присутствуют выбросы! Заменяю их на значения медианы.')
        
        for i in range(len(values_init)):
            if values_init[i] < lower_limit:
                values_init[i] = med
            elif values_init[i] > upper_limit:
                values_init[i] = med
        
        #Замена выбросов
        data[target].replace(list(data[target]), values_init, inplace = True)
        return data
    

    def replace_outliers_with_median(self, lower_quantile: float = 0.05, upper_quantile: float = 0.95) -> list:
        """
            Функция для замены выбросов на значения медианы.
            Выбросы определяются как значения за пределами [lower_quantile, upper_quantile].
            Args:
                data (list): Входной список числовых данных.
                lower_quantile (float): Нижний квантиль (по умолчанию 0.05).
                upper_quantile (float): Верхний квантиль (по умолчанию 0.95).

            Returns:
                list: Список с обработанными данными.
        """
        data_array = np.array(self.series.tolist())
        lower_bound = np.quantile(data_array, lower_quantile)
        upper_bound = np.quantile(data_array, upper_quantile)

        median_val = np.median(data_array)

        # Создаем копию, чтобы не менять исходные данные
        processed_data = data_array.copy()
        processed_data[(data_array < lower_bound) | (data_array > upper_bound)] = median_val

        return processed_data.tolist()


class TimeSeriesTrendAnalyze:
    """
        Класс для анализа тренда Временного Ряда
    """
    def __init__(self, data):
        """
            data: DataFrame, в котором два столбца: дата и время
        """
        self.data = data


    def analyze_trend_comprehensive(self, date_column: str = 'Date', target_column: str = 'Share'):
        """
            Функция для комплексного анализа тренда.
            Args:
                data: ВР, в котором два столбца: дата и время
            Returns:
                results: dict: словарь с оценкой силы тренда
        """
        series = self.data.copy()
        series.set_index(date_column, inplace = True)

        x = np.arange(len(series))
        # Тест Манна-Кендалла: тест, который оценивает наличие/отсутствие монотонного тренда ВР
        tau, p_value = kendalltau(x, series[target_column])
        #Значение 1 указывает на положительную монотонность, -1 — на отрицательную, а 0 — на отсутствие монотонности. 
        if tau > 0.5 and tau <= 1:
            print('Присутствует положительный монотонный тренд.')
        elif tau < - 0.5:
            print('Присутствует отрицательный монотонный тренд.')
        elif tau >= -0.5 and tau <= 0.5:
            print('Монотонный тренд отсутствует.')
        return tau
    

    def analyze_first_diff(self, target_column: str = 'Share', criteria = 0.15):
        """
            Функция для анализа первых разностей. Если после взятие первых разностей среднее значений ряда не стало близко к нулю,
            то это индикатор сильного нелинейного тренда.
        """
        values_init = list(self.data[target_column])
        diff_series = np.diff(values_init)
        mean_abs_diff = np.mean(np.abs(diff_series))
        diff_ratio = mean_abs_diff / np.mean(np.abs(values_init))
        if diff_ratio > criteria:
            print('Присутствует сильный нелинейный тренд!')
        return mean_abs_diff, diff_ratio
    

    def extract_trend_with_ma(self, window_size, target_column: str = 'Share', center = True, min_periods = None):
        """
            Выделяет тренд с помощью скользящего среднего.
            Args:
                data: Временной ряд
                window_size: размер окна
                center: центрирование окна
                min_periods: минимальное количество точек для вычисления
            Returns:
                Series с выдеделенным трендом
        """
        values_init = list(self.data[target_column])
        if min_periods is not None:
            min_periods = window_size // 2
        trend = self.data[target_column].rolling(window = window_size, center = center, min_periods = min_periods).mean()
        detrend_series = values_init - trend
        return detrend_series, trend
    

    @staticmethod
    def forecast_moving_average_trend(moving_avg, n_steps, window_size):
        """
        Прогнозирование тренда на основе скользящей средней
        """
        n = len(moving_avg)
        
        # Линейная экстраполяция последних значений тренда
        last_values = moving_avg[-window_size//2:]  # Берем последние значения
        x = np.arange(len(last_values))
        slope, intercept = np.polyfit(x, last_values, 1)
        trend_forecast = slope * np.arange(n_steps) + intercept
        
        return trend_forecast