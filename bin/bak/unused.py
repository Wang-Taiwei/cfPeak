import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import seaborn as sns
sns.set()
import pandas as pd

class FeatureSelection(object):
    def __init__(self, matrix, 
            sample_classes,
            output_dir,
            remove_zero_features=None,
            top_features_by_median=None,
            transpose=False,
            positive_class=None,
            negative_class=None,
            use_log=False,
            scaler=None,
            compute_sample_weight=False,
            method='logistic_regression',
            rfe=False,
            n_select=None,
            resample_method='jackknife',
            jackknife_max_runs=100,
            jackknife_remove=0.2,
            bootstrap_max_runs=100,
            **kwargs):
        self.matrix_file = matrix
        self.sample_classes_file = sample_classes
        self.output_dir = output_dir
        self.remove_zero_features = remove_zero_features
        self.top_features_by_median = top_features_by_median
        self.transpose = transpose
        self.positive_class = positive_class
        self.negative_class = negative_class
        self.use_log = use_log
        self.scaler = scaler
        self.compute_sample_weight = compute_sample_weight
        self.rfe = rfe
        self.method = method
        self.n_select = n_select
        self.resample_method = resample_method
        self.jackknife_max_runs = jackknife_max_runs
        self.jackknife_remove = jackknife_remove
        self.bootstrap_max_runs = bootstrap_max_runs
        self.logger = logging.getLogger('FeatureSelection')

        if not os.path.exists(self.output_dir):
            self.logger.info('create output directory: ' + self.output_dir)
            os.makedirs(self.output_dir)
    
    @define_step(inputs=['matrix_file'], outputs=['X', 'sample_classes'])
    def read_data(self):
        self.X = pd.read_table(self.matrix_file, index_col=0)
        if self.transpose:
            self.logger.info('transpose feature matrix')
            self.X = self.X.T
        self.logger.info('{} samples, {} features'.format(*self.X.shape))
        self.logger.info('read sample classes: ' + self.sample_classes_file)
        self.sample_classes = pd.read_table(self.sample_classes_file, header=None, 
            names=['sample_id', 'sample_class'], index_col=0)
        self.sample_classes = self.sample_classes.iloc[:, 0]
    
    @define_step(inputs=['X'], outputs=['feature_names', 'n_features'])
    def filter_features(self):
        if self.remove_zero_features is not None:
            self.X = self.X.loc[:, np.isclose(m, 0).sum(axis=0) >= (m.shape[0]*self.remove_zero_features)]
        if self.top_features_by_median is not None:
            nonzero_samples = (self.X > 0).sum(axis=0)
            counts_geomean = np.exp(np.sum(np.log(np.maximum(self.X, 1)), axis=0)/nonzero_samples)
            self.X = self.X.loc[:, counts_geomean.sort_values(ascending=False)[:self.top_features_by_median].index.values]
        self.feature_names = self.X.columns.values
        self.n_features = self.X.shape[1]
        self.logger.info('{} features after filtering'.format(self.n_features))
        self.logger.info('features: {} ...'.format(str(self.feature_names[:3])))
    
    @define_step(inputs=['X', 'positive_class', 'negative_class'],
        outputs=['X', 'y', 'n_samples', 'sample_ids', 'X_raw'])
    def select_samples(self):
        if (self.positive_class is not None) and (self.negative_class is not None):
            self.positive_class = self.positive_class.split(',')
            self.negative_class = self.negative_class.split(',')
        else:
            unique_classes = np.unique(self.sample_classes.values)
            if len(unique_classes) != 2:
                raise ValueError('expect 2 classes but {} classes found'.format(len(unique_classes)))
            self.positive_class, self.negative_class = unique_classes
        self.positive_class = np.atleast_1d(self.positive_class)
        self.negative_class = np.atleast_1d(self.negative_class)

        self.logger.info('positive class: {}, negative class: {}'.format(self.positive_class, self.negative_class))
        X_pos = self.X.loc[self.sample_classes[self.sample_classes.isin(self.positive_class)].index.values]
        X_neg = self.X.loc[self.sample_classes[self.sample_classes.isin(self.negative_class)].index.values]
        self.logger.info('number of positive samples: {}, negative samples: {}, class ratio: {}'.format(
            X_pos.shape[0], X_neg.shape[0], float(X_pos.shape[0])/X_neg.shape[0]))
        self.X = pd.concat([X_pos, X_neg], axis=0)
        self.y = np.zeros(self.X.shape[0], dtype=np.int32)
        self.y[X_pos.shape[0]:] = 1
        self.X_raw = self.X
        self.n_samples = self.X.shape
        self.sample_ids = self.X.index.values

    @define_step(inputs=['X', 'use_log', 'scaler'], outputs=['X'])
    def scale_features(self):
        if self.use_log:
            self.logger.info('apply log2 to feature matrix')
            self.X = np.log2(self.X + 1)

        if self.scaler == 'zscore':
            scaler = StandardScaler()
        elif self.scaler == 'robust':
            scaler = RobustScaler()
        elif self.scaler == 'min_max':
            scaler = MinMaxScaler()
        elif self.scaler == 'max_abs':
            scaler = MaxAbsScaler()
        self.logger.info('scale features using {}'.format(self.scaler))
        self.X = scaler.fit_transform(self.X)

    @define_step(inputs=['X', 'method', 'resample_method'], 
        outputs=['estimator', 'robust_estimator', 'compute_sample_weight'])
    def train_model(self):
        if np.any(np.isnan(self.X)):
            self.logger.info('nan values found in features')
        self.estimator = None
        grid_search = None
        self.logger.info('use {} to select features'.format(self.method))
        if self.method == 'r_test':
            self.estimator = TTestEstimator()
        elif self.method == 'logistic_regression':
            self.estimator = LogisticRegression()
            grid_search = {'C': [1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1, 1e2, 1e3, 1e4, 1e5]}
        elif self.method == 'random_forest':
            self.estimator = RandomForestClassifier()
            grid_search = {'max_depth': list(range(2, 10))}
        elif self.method == 'linear_svm':
            self.estimator = LinearSVC()
            grid_search = {'C': [1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1, 1e2, 1e3, 1e4, 1e5]}
        else:
            raise ValueError('unknown feature selection method: {}'.format(self.method))

        resampler_args = {}
        if self.resample_method == 'jackknife':
            resampler_args = {'max_runs': self.jackknife_max_runs,
                'remove': self.jackknife_remove
            }
        elif self.resample_method == 'bootstrap':
            resampler_args = {'max_runs': self.bootstrap_max_runs}

        self.robust_estimator = RobustEstimator(self.estimator, n_select=self.n_select, 
            grid_search=grid_search, resample_method='bootstrap',
            rfe=self.rfe, **resampler_args)

        sample_weight = None
        if self.compute_sample_weight:
            sample_weight = compute_sample_weight('balanced', self.y)
        self.robust_estimator.fit(self.X, self.y, sample_weight=sample_weight)
        self.estimator = self.robust_estimator.estimator_

    @define_step(inputs=['estimator', 'X', 'y'])
    def evaluate_model(self):
        self.logger.info('evaluate the model')
        features = pd.read_table(os.path.join(self.output_dir, 'features.txt'),
            header=None).iloc[:, 0].values
        feature_index = pd.Series(np.arange(self.n_features), 
            index=self.feature_names).loc[features]
        
        y_pred = self.estimator.predict(self.X[:, feature_index])
        y_score = self.estimator.predict_proba(self.X[:, feature_index])[:, 1]
        roc_auc = roc_auc_score(self.y, y_score)
        fpr, tpr, thresholds = roc_curve(self.y, y_score)

        sns.set_style('whitegrid')
        fig, ax = plt.subplots(figsize=(7, 7))
        ax.plot(fpr, tpr, label='AUC = {:.4f}'.format(roc_auc))
        ax.plot([0, 1], [0, 1], linestyle='dashed', color='gray', linewidth=0.8)
        ax.set_xlabel('False positive rate')
        ax.set_ylabel('True positive rate')
        ax.legend()
        plt.savefig(os.path.join(self.output_dir, 'roc_curve.pdf'))
        plt.close()

    @define_step(inputs=['estimator'])
    def save_model(self):
        self.logger.info('save model')
        with open(os.path.join(self.output_dir, 'model.pkl'), 'wb') as f:
            pickle.dump(self.estimator, f)
    
    @define_step(outputs=['estimator'])
    def load_model(self):
        self.logger.info('load model')
        with open(os.path.join(self.output_dir, 'model.pkl'), 'rb') as f:
            self.estimator = pickle.load(f)
    
    @define_step(inputs=['estimator'])
    def save_params(self):
        self.logger.info('save model parameters')
        with open(os.path.join(self.output_dir, 'params.json'), 'w') as f:
            json.dump(self.estimator.get_params(), f, indent=2)
    
    def save_matrix(self):
        self.logger.info('save matrix')
        df = pd.DataFrame(self.X, columns=self.feature_names, index=self.sample_ids)
        df.index.name = 'sample'
        df.to_csv(os.path.join(self.output_dir, 'matrix.txt'),
            sep='\t', index=True, header=True)
        data = pd.Series(self.y, index=self.sample_ids)
        data.to_csv(os.path.join(self.output_dir, 'labels.txt'),
            sep='\t', index=True, header=False)
    
    @define_step(inputs=['output_dir', 'n_features', 'feature_names', 'X', 'y'])
    def single_feature_metrics(self):
        self.logger.info('compute metrics for selected features independently')
        features = pd.read_table(os.path.join(self.output_dir, 'features.txt'),
            header=None).iloc[:, 0].values
        feature_index = pd.Series(np.arange(self.n_features), 
            index=self.feature_names).loc[features]
        metrics = {}
        scorers = {'roc_auc': roc_auc_score}
        for metric in ['roc_auc']:
            metrics[metric] = np.zeros(len(features))
            for i, i_feature in enumerate(feature_index):
                metrics[metric][i] = scorers[metric](self.y, self.X[:, i_feature])
        metrics = pd.DataFrame(metrics, index=features)
        metrics.index.name = 'feature'
        metrics.to_csv(os.path.join(self.output_dir, 'single_feature_metrics.txt'),
            sep='\t', index=True, header=True)

    @define_step(inputs=['estimator'])
    def plot_feature_importance(self):
        self.logger.info('plot feature importance')
        features = pd.read_table(os.path.join(self.output_dir, 'features.txt'),
            header=None).iloc[:, 0].values
        
        feature_importance = pd.read_table(os.path.join(self.output_dir, 'feature_importances.txt'),
            names=['feature', 'feature_importance'], header=None)
        feature_importance = feature_importance.iloc[np.argsort(-feature_importance['feature_importance'].values), :]
        print(feature_importance.head())
        sns.set_style('whitegrid')
        fig, ax = plt.subplots(figsize=(15, 20))
        sns.barplot('feature_importance', 'feature', color='gray',
            data=feature_importance, ax=ax)
        plt.subplots_adjust(left=0.3)
        plt.savefig(os.path.join(self.output_dir, 'feature_importances_refitted.pdf'))
        plt.close()

        feature_importance_matrix = pd.read_table(os.path.join(self.output_dir, 'feature_importance_matrix.txt'))
        feature_importance_matrix = feature_importance_matrix.loc[:, features]
        feature_importance_matrix = feature_importance_matrix.iloc[:, np.argsort(-feature_importance_matrix.mean(axis=0).values)]
        data = pd.melt(feature_importance_matrix, var_name='feature', value_name='feature_importance')
        sns.set_style('whitegrid')
        fig, ax = plt.subplots(figsize=(15, 25))
        sns.barplot('feature_importance', 'feature', color='gray', ci='sd',
            data=data, ax=ax, errwidth=1, capsize=0.2)
        plt.subplots_adjust(left=0.2)
        plt.savefig(os.path.join(self.output_dir, 'feature_importances_estimate.pdf'))
        plt.close()



    